import asyncio
import hashlib
import hmac
import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class Principal:
    name: str
    tenant: str
    role: str
    digest: str
    expires_at: datetime | None = None


class KeyStore:
    def __init__(self, path, require_expiry=False):
        self.path, self.require_expiry = path, require_expiry
        self.lock = threading.Lock()
        self.signature = None
        self._reload()

    def _reload(self):
        stat = self.path.stat()
        signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        if signature == self.signature:
            return
        if stat.st_size > 64_000:
            raise ValueError("Credential configuration too large")
        records = json.loads(self.path.read_text())
        if not isinstance(records, list) or not 1 <= len(records) <= 100:
            raise ValueError("Expected 1..100 credential records")
        loaded, digests = [], set()
        for r in records:
            if r["role"] not in {"admin", "analyst", "scorer", "metrics"}:
                raise ValueError("Unknown credential role")
            if (not re.fullmatch(r"[0-9a-f]{64}", r["sha256"])
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", r["tenant"])
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", r["name"])
                    or r["sha256"] in digests):
                raise ValueError("Invalid credential configuration")
            digests.add(r["sha256"])
            expiry = datetime.fromisoformat(r["expires_at"].replace("Z", "+00:00")) if r.get("expires_at") else None
            if (expiry and expiry.utcoffset() is None) or (self.require_expiry and expiry is None):
                raise ValueError("Timezone-aware credential expiry required")
            if not isinstance(r.get("revoked", False), bool):
                raise ValueError("Invalid revoked flag")
            if not r.get("revoked", False):
                loaded.append(Principal(r["name"], r["tenant"], r["role"], r["sha256"], expiry))
        self.records, self.signature = loaded, signature

    def authenticate(self, token):
        with self.lock:
            try:
                self._reload()
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                raise HTTPException(503, "Credential service unavailable") from None
            records = self.records
        digest = hashlib.sha256(token.encode()).hexdigest()
        principal = None
        for r in records:
            if hmac.compare_digest(r.digest, digest) and (
                    r.expires_at is None or r.expires_at > datetime.now(timezone.utc)):
                principal = r
        return principal


class RateLimiter:
    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.lock, self.local = threading.Lock(), OrderedDict()

    def allowed(self, key, limit):
        bucket = int(time.time() // 60)
        if self.redis:
            try:
                # Atomically increment and expire; no permanent keys after a crashed client.
                n = self.redis.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],120) end; return n",
                                    1, f"rate:{key}:{bucket}")
                return n <= limit
            except Exception:
                raise HTTPException(503, "Rate-limit service unavailable") from None
        with self.lock:
            k = (key, bucket)
            self.local[k] = self.local.get(k, 0) + 1
            self.local.move_to_end(k)
            while len(self.local) > 10000:
                self.local.popitem(last=False)
            return self.local[k] <= limit


def authorize(*roles):
    def check(request: Request):
        state = request.app.state
        ip = request.client.host if request.client else "unknown"
        ip_digest = hashlib.sha256(ip.encode()).hexdigest()[:24]
        if not state.limiter.allowed("ip:" + ip_digest, 600):
            raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) > 256:
            raise HTTPException(401, "Valid bearer token required", headers={"WWW-Authenticate": "Bearer"})
        principal = state.keys.authenticate(auth[7:])
        if not principal:
            raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})
        if principal.role not in roles:
            raise HTTPException(403, "Insufficient permissions")
        if not state.limiter.allowed("key:" + principal.digest, state.settings.rate_per_minute):
            raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
        return principal
    return check


class RequestBoundary:
    """Bound streamed bodies as well as Content-Length. Security headers on errors too."""
    def __init__(self, app, max_bytes=8192, production=False, body_timeout_seconds=5):
        self.app, self.max_bytes, self.production = app, max_bytes, production
        self.body_timeout_seconds = body_timeout_seconds

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        response_started = False
        async def secured(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"content-security-policy", b"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                    (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"), (b"cache-control", b"no-store"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"x-process-time-ms", f"{(time.perf_counter()-started)*1000:.2f}".encode()),
                ])
                if self.production:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = headers
            await send(message)
        headers = Headers(scope=scope)
        async def reject(code, detail):
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, secured)
        if len(scope.get("query_string", b"")) > 2048:
            return await reject(414, "Query too large")
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return await reject(415, "application/json required")
            if headers.get("content-encoding", "identity") != "identity":
                return await reject(415, "Compressed request bodies unsupported")
            try:
                length = int(headers.get("content-length", "0"))
                if length < 0:
                    raise ValueError
            except ValueError:
                return await reject(400, "Invalid Content-Length")
            if length > self.max_bytes:
                return await reject(413, "Request body too large")
            data = bytearray()
            try:
                async with asyncio.timeout(self.body_timeout_seconds):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        data.extend(message.get("body", b""))
                        if len(data) > self.max_bytes:
                            return await reject(413, "Request body too large")
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "Request body timed out")
            try:
                strict_json(data)
            except (ValueError, RecursionError, UnicodeDecodeError):
                return await reject(400, "Invalid or ambiguous JSON")
            sent = False
            original_receive = receive
            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(data), "more_body": False}
                return await original_receive()
            receive = replay
        try:
            await self.app(scope, receive, secured)
        except Exception as exc:
            if response_started:
                raise
            logging.getLogger("sentinel").error("Request failed (%s)", type(exc).__name__)
            await reject(500, "Internal error")


def strict_json(data):
    """Reject duplicate keys and non-standard NaN/Infinity before schema validation."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("Non-finite JSON")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON")
        return number

    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant, parse_float=finite_float)
