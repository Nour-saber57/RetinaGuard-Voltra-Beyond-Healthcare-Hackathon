from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer


class DiabetesType(str, Enum):
    TYPE_1 = "Type 1"
    TYPE_2 = "Type 2"
    GESTATIONAL = "Gestational diabetes"
    OTHER = "Other"
    UNKNOWN = "Unknown / not sure"


class CaseCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr = Field(max_length=254)
    age: int = Field(ge=0, le=120)
    diabetes_type: DiabetesType


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    age: int
    diabetes_type: DiabetesType
    status: str
    prediction: str | None
    created_at: datetime

    @field_serializer("created_at")
    def format_created_at(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)

        return f"{value:%B} {value.day}, {value.year} at {value:%I:%M %p} UTC"