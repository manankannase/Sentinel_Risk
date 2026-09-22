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
    currency: Literal["USD"] = "USD"
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    channel: Literal["ecommerce", "pos", "atm"]
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value.astimezone(timezone.utc)


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Annotated[int, Field(strict=True, ge=0, le=1)]
