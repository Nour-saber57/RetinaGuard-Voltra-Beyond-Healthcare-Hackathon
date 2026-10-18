from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, model_validator


class DiabetesType(str, Enum):
    NOT_SPECIFIED = "Not specified"
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
    age: int | None = Field(default=None, ge=0, le=120)
    diabetes_type: DiabetesType


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: EmailStr
    age: int | None
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


class PatientCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=100)
    email: EmailStr = Field(max_length=254)
    phone: str = Field(pattern=r"^\+201[0125]\d{8}$", max_length=20)
    password: str = Field(min_length=8, max_length=128)


class PatientLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ClinicLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    account: dict


class PatientResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    phone: str
    diabetes_type: DiabetesType
    awareness_completed: bool
    results_appointment: str | None
    followup_appointment: str | None


class PatientUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2, max_length=100)
    email: EmailStr | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, pattern=r"^\+201[0125]\d{8}$", max_length=20)
    diabetes_type: DiabetesType | None = None


class ProgressUpdate(BaseModel):
    awareness_completed: bool | None = None
    results_appointment: str | None = Field(default=None, max_length=20)
    followup_appointment: str | None = Field(default=None, max_length=20)


class HealthMeasurementInput(BaseModel):
    kind: Literal["weight", "a1c", "bp"]
    measured_on: date
    value: float = Field(gt=0)
    diastolic: float | None = None

    @model_validator(mode="after")
    def validate_measurement(self):
        limits = {"weight": (1, 500), "a1c": (1, 30), "bp": (30, 350)}
        low, high = limits[self.kind]
        if not low <= self.value <= high:
            raise ValueError("Measurement is outside the accepted input range")
        if self.kind == "bp":
            if self.diastolic is None or not 20 <= self.diastolic <= 250:
                raise ValueError("Blood pressure requires a valid diastolic value")
            if not self.value.is_integer() or not self.diastolic.is_integer() or self.value <= self.diastolic:
                raise ValueError("Blood pressure values must be whole numbers with systolic higher than diastolic")
        elif self.diastolic is not None:
            raise ValueError("Diastolic is only valid for blood pressure")
        return self


class HealthMeasurementResponse(BaseModel):
    id: int
    kind: Literal["weight", "a1c", "bp"]
    measured_on: date
    value: float
    diastolic: float | None


class HealthMeasurementBatch(BaseModel):
    readings: list[HealthMeasurementInput] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_measurement_types(self):
        keys = [(item.kind, item.measured_on) for item in self.readings]
        if len(keys) != len(set(keys)):
            raise ValueError("Only one reading per measurement type and date is allowed")
        return self


class ClinicResponse(BaseModel):
    id: str
    name: str
    area: str
    address: str
    latitude: float
    longitude: float
    fee: int
    hours: str
    specialty: str


class AvailabilityResponse(BaseModel):
    date: date
    slots: list[str]


class AppointmentCreate(BaseModel):
    clinic_id: str = Field(min_length=1, max_length=20)
    date: date
    time: time


class AppointmentResponse(BaseModel):
    id: int
    reference: str
    clinic_id: str
    patient_name: str
    date: date
    time: time
    status: str
    confirmation_email_sent: bool | None = None


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1200)


class PatientChatRequest(BaseModel):
    consent: bool
    message: str = Field(min_length=1, max_length=1200)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)


class PatientChatResponse(BaseModel):
    reply: str