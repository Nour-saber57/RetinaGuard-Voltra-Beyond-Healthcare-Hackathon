from datetime import date, datetime, time, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    age: Mapped[int | None] = mapped_column(nullable=True)
    diabetes_type: Mapped[str] = mapped_column(String(20))

    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254))
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True
    )

    image_path: Mapped[str | None] = mapped_column(
        String, nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(30), default="new"
    )

    prediction: Mapped[str | None] = mapped_column(
        String, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    diabetes_type: Mapped[str] = mapped_column(String(30), default="Not specified")
    awareness_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    results_appointment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    followup_appointment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class HealthMeasurement(Base):
    __tablename__ = "health_measurements"
    __table_args__ = (
        UniqueConstraint("patient_id", "kind", "measured_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    measured_on: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    diastolic: Mapped[float | None] = mapped_column(Float, nullable=True)


class Clinic(Base):
    __tablename__ = "clinics"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    area: Mapped[str] = mapped_column(String(80))
    address: Mapped[str] = mapped_column(String(180))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    fee: Mapped[int] = mapped_column(Integer)
    hours: Mapped[str] = mapped_column(String(120))
    specialty: Mapped[str] = mapped_column(String(160))


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index(
            "uq_confirmed_appointment_slot",
            "clinic_id",
            "appointment_date",
            "appointment_time",
            unique=True,
            sqlite_where=text("status = 'Confirmed'"),
            postgresql_where=text("status = 'Confirmed'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    clinic_id: Mapped[str] = mapped_column(ForeignKey("clinics.id"))
    appointment_date: Mapped[date] = mapped_column(Date)
    appointment_time: Mapped[time] = mapped_column(Time)
    status: Mapped[str] = mapped_column(String(20), default="Confirmed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )