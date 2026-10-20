"""Issue or revoke expiring API keys with atomic configuration replacement."""
import argparse
import fcntl
import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinel.security import KeyStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keys-file', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    issue = commands.add_parser('issue')
    issue.add_argument('--name', required=True)
    issue.add_argument('--tenant', required=True)
    issue.add_argument('--role', choices=['admin', 'analyst', 'scorer', 'metrics'], required=True)
    issue.add_argument('--days', type=int, default=90)
    issue.add_argument('--token-file', type=Path, required=True)
    revoke = commands.add_parser('revoke')
    revoke.add_argument('--name', required=True)
    revoke.add_argument('--tenant', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    path = args.keys_file
    if args.command == 'issue' and not 1 <= args.days <= 90:
        parser.error('Credential lifetime must be 1..90 days')
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = json.loads(path.read_text()) if path.exists() else []
        if args.command == 'issue':
            if any(r['name'] == args.name and r['tenant'] == args.tenant for r in records):
                parser.error('Choose a new credential name for rotation; revoke the previous key after rollout')
            token = secrets.token_urlsafe(32)
            records.append({'name': args.name, 'tenant': args.tenant, 'role': args.role,
                            'sha256': hashlib.sha256(token.encode()).hexdigest(),
                            'expires_at': (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()})
        else:
            matches = [r for r in records if r['name'] == args.name and r['tenant'] == args.tenant]
            if not matches:
                parser.error('Credential not found')
            for record in matches:
                record['revoked'] = True
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.keys-')
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(records, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            KeyStore(temporary)
            if args.command == 'issue':
                # Exclusive create prevents overwriting another credential.
                with args.token_file.open('x') as handle:
                    handle.write(token + '\n')
            temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o640)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    print('Credential configuration updated. No token printed.')


if __name__ == '__main__':
    main()
