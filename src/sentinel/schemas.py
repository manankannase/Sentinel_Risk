from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class TransactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: UUID
    customer_id: Identifier
    merchant_id: Identifier
    device_id: Identifier
    amount_minor: Annotated[int, Field(strict=True, ge=1, le=100_000_000)]
    currency: Literal["INR"] = "INR"
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    channel: Literal["ecommerce", "pos", "atm", "upi", "imps", "neft", "rtgs"]
    occurred_at: datetime

    @field_validator("occurred_at", mode="before")
    @classmethod
    def timestamp_string(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Use an ISO-8601 timestamp with timezone")
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value.astimezone(timezone.utc)


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Annotated[int, Field(strict=True, ge=0, le=1)]


class FeedbackCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_label: Annotated[int, Field(strict=True, ge=0, le=1)]
    label: Annotated[int, Field(strict=True, ge=0, le=1)]
    reason: Annotated[str, Field(min_length=10, max_length=500, pattern=r"\S")]
