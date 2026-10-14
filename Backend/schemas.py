from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CaseCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr = Field(max_length=254)


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    status: str
    prediction: str | None
    created_at: datetime