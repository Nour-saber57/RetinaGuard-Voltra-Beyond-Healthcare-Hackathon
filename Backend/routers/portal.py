from datetime import date, datetime, time, timedelta
import logging
import os
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from email_service import send_email
from models import Appointment, Clinic, HealthMeasurement, Patient
from schemas import (
    AppointmentCreate,
    AppointmentResponse,
    AvailabilityResponse,
    ClinicLogin,
    ClinicResponse,
    DiabetesType,
    HealthMeasurementInput,
    HealthMeasurementBatch,
    HealthMeasurementResponse,
    PatientCreate,
    PatientLogin,
    PatientResponse,
    PatientUpdate,
    ProgressUpdate,
    TokenResponse,
)
from security import (
    Principal,
    current_principal,
    hash_password,
    issue_token,
    require_clinic,
    require_patient,
    verify_password,
)

router = APIRouter(tags=["Portal"])
logger = logging.getLogger(__name__)
CAIRO = ZoneInfo("Africa/Cairo")
PROVIDERS = [
    Clinic(id="bright", name="Bright Vision Eye Center", area="Nasr City", address="Demo location · Nasr City, Cairo", latitude=30.0566, longitude=31.3383, fee=450, hours="Saturday–Thursday · 09:00–17:00", specialty="Retinal screening & ophthalmology"),
    Clinic(id="clear", name="ClearView Eye Clinic", area="Heliopolis", address="Demo location · Heliopolis, Cairo", latitude=30.091, longitude=31.322, fee=500, hours="Saturday–Thursday · 10:00–18:00", specialty="Diabetes eye checks & ophthalmology"),
    Clinic(id="noor", name="Al Noor Eye Hospital", area="Dokki", address="Demo location · Dokki, Giza", latitude=30.038, longitude=31.211, fee=400, hours="Saturday–Thursday · 09:00–17:00", specialty="Retinal screening & ophthalmology"),
]


def seed_clinics(db: Session) -> None:
    for provider in PROVIDERS:
        if db.get(Clinic, provider.id) is None:
            db.add(Clinic(
                id=provider.id,
                name=provider.name,
                area=provider.area,
                address=provider.address,
                latitude=provider.latitude,
                longitude=provider.longitude,
                fee=provider.fee,
                hours=provider.hours,
                specialty=provider.specialty,
            ))
    db.commit()


def patient_data(patient: Patient) -> dict:
    return PatientResponse(
        id=patient.id,
        name=patient.name,
        email=patient.email,
        phone=patient.phone,
        diabetes_type=patient.diabetes_type,
        awareness_completed=patient.awareness_completed,
        results_appointment=patient.results_appointment,
        followup_appointment=patient.followup_appointment,
    ).model_dump(mode="json")


def token_response(token: str, account: dict) -> TokenResponse:
    return TokenResponse(access_token=token, account=account)


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    email = str(payload.email).lower()
    if db.scalar(select(Patient).where(Patient.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    patient = Patient(
        name=payload.name,
        email=email,
        phone=payload.phone,
        password_hash=hash_password(payload.password),
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return token_response(issue_token(str(patient.id), "patient"), patient_data(patient))


@router.post("/auth/login", response_model=TokenResponse)
def login_patient(payload: PatientLogin, db: Session = Depends(get_db)):
    patient = db.scalar(select(Patient).where(Patient.email == str(payload.email).lower()))
    if patient is None or not verify_password(payload.password, patient.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    return token_response(issue_token(str(patient.id), "patient"), patient_data(patient))


@router.post("/auth/clinic/login", response_model=TokenResponse)
def login_clinic(payload: ClinicLogin):
    expected_email = os.getenv("CLINIC_DEMO_EMAIL", "clinic@retinaguard.demo").lower()
    expected_password = os.getenv("CLINIC_DEMO_PASSWORD", "DemoClinic2026!")
    if str(payload.email).lower() != expected_email or not __import__("hmac").compare_digest(payload.password, expected_password):
        raise HTTPException(status_code=401, detail="Clinic email or password is incorrect")
    return token_response(issue_token(expected_email, "clinic"), {"role": "clinic", "email": expected_email})


@router.get("/auth/me")
def get_current_account(
    principal: Principal = Depends(current_principal),
):
    if principal.patient:
        return {"role": "patient", **patient_data(principal.patient)}
    return {"role": "clinic", "email": principal.subject}


@router.get("/clinic/patients", response_model=list[PatientResponse])
def get_clinic_patients(
    _: Principal = Depends(require_clinic),
    db: Session = Depends(get_db),
):
    return db.scalars(select(Patient).order_by(Patient.name)).all()


@router.patch("/patients/me", response_model=PatientResponse)
def update_patient(
    payload: PatientUpdate,
    patient: Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    changes = payload.model_dump(exclude_unset=True)
    if "email" in changes and changes["email"] is not None:
        email = str(changes["email"]).lower()
        existing = db.scalar(select(Patient).where(Patient.email == email, Patient.id != patient.id))
        if existing:
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        changes["email"] = email
    for key, value in changes.items():
        if value is not None:
            setattr(patient, key, value.value if isinstance(value, DiabetesType) else value)
    db.commit()
    db.refresh(patient)
    return patient


@router.patch("/patients/me/progress", response_model=PatientResponse)
def update_progress(
    payload: ProgressUpdate,
    patient: Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    changes = payload.model_dump(exclude_unset=True)
    result_reference = changes.get("results_appointment", patient.results_appointment)
    followup_reference = changes.get("followup_appointment", patient.followup_appointment)
    if followup_reference is not None and followup_reference != result_reference:
        raise HTTPException(status_code=400, detail="Follow-up progress must use the same appointment as results")
    for field in ("results_appointment", "followup_appointment"):
        reference = changes.get(field)
        if reference is not None:
            appointment = db.scalar(select(Appointment).where(
                Appointment.reference == reference,
                Appointment.patient_id == patient.id,
                Appointment.status == "Confirmed",
            ))
            if appointment is None:
                raise HTTPException(status_code=400, detail="Progress must reference your confirmed appointment")
    for key, value in changes.items():
        setattr(patient, key, value)
    db.commit()
    db.refresh(patient)
    return patient


@router.get("/health/readings", response_model=list[HealthMeasurementResponse])
def get_health_readings(patient: Patient = Depends(require_patient), db: Session = Depends(get_db)):
    return db.scalars(select(HealthMeasurement).where(
        HealthMeasurement.patient_id == patient.id,
    ).order_by(HealthMeasurement.measured_on, HealthMeasurement.id)).all()


@router.post("/health/readings", response_model=HealthMeasurementResponse, status_code=201)
def create_health_reading(
    payload: HealthMeasurementInput,
    patient: Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    if payload.measured_on > datetime.now(CAIRO).date():
        raise HTTPException(status_code=422, detail="Measurement date cannot be in the future")
    reading = HealthMeasurement(patient_id=patient.id, **payload.model_dump())
    db.add(reading)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="This measurement already exists on that date") from error
    db.refresh(reading)
    return reading


@router.post("/health/readings/batch", response_model=list[HealthMeasurementResponse], status_code=201)
def create_health_readings(
    payload: HealthMeasurementBatch,
    patient: Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    today = datetime.now(CAIRO).date()
    if any(item.measured_on > today for item in payload.readings):
        raise HTTPException(status_code=422, detail="Measurement date cannot be in the future")
    readings = [
        HealthMeasurement(patient_id=patient.id, **item.model_dump())
        for item in payload.readings
    ]
    db.add_all(readings)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="A measurement already exists for one of these dates") from error
    for reading in readings:
        db.refresh(reading)
    return readings


@router.put("/health/readings/{reading_id}", response_model=HealthMeasurementResponse)
def update_health_reading(
    reading_id: int,
    payload: HealthMeasurementInput,
    patient: Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    reading = db.scalar(select(HealthMeasurement).where(
        HealthMeasurement.id == reading_id,
        HealthMeasurement.patient_id == patient.id,
    ))
    if reading is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    if payload.measured_on > datetime.now(CAIRO).date():
        raise HTTPException(status_code=422, detail="Measurement date cannot be in the future")
    for key, value in payload.model_dump().items():
        setattr(reading, key, value)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="This measurement already exists on that date") from error
    db.refresh(reading)
    return reading


@router.delete("/health/readings/{reading_id}", status_code=204)
def delete_health_reading(
    reading_id: int,
    patient: Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    reading = db.scalar(select(HealthMeasurement).where(
        HealthMeasurement.id == reading_id,
        HealthMeasurement.patient_id == patient.id,
    ))
    if reading is None:
        raise HTTPException(status_code=404, detail="Measurement not found")
    db.delete(reading)
    db.commit()


@router.get("/clinics", response_model=list[ClinicResponse])
def get_clinics(db: Session = Depends(get_db)):
    return db.scalars(select(Clinic).order_by(Clinic.name)).all()


def available_slots(clinic: Clinic, target: date, db: Session) -> list[str]:
    now = datetime.now(CAIRO)
    if target < now.date() or target > now.date() + timedelta(days=14) or target.weekday() == 4:
        return []
    start_hour = 10 if clinic.id == "clear" else 9
    slots = [time(hour, 0) for hour in range(start_hour, start_hour + 8)]
    booked = set(db.scalars(select(Appointment.appointment_time).where(
        Appointment.clinic_id == clinic.id,
        Appointment.appointment_date == target,
        Appointment.status == "Confirmed",
    )).all())
    local_time = now.time().replace(tzinfo=None, second=0, microsecond=0)
    return [slot.strftime("%H:%M") for slot in slots if slot not in booked and (target != now.date() or slot > local_time)]


@router.get("/clinics/{clinic_id}/availability", response_model=list[AvailabilityResponse])
def clinic_availability(clinic_id: str, db: Session = Depends(get_db)):
    clinic = db.get(Clinic, clinic_id)
    if clinic is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    today = datetime.now(CAIRO).date()
    return [
        AvailabilityResponse(date=today + timedelta(days=offset), slots=available_slots(clinic, today + timedelta(days=offset), db))
        for offset in range(15)
    ]


def appointment_data(
    appointment: Appointment,
    db: Session,
    confirmation_email_sent: bool | None = None,
) -> dict:
    patient = db.get(Patient, appointment.patient_id)
    return AppointmentResponse(
        id=appointment.id,
        reference=appointment.reference,
        clinic_id=appointment.clinic_id,
        patient_name=patient.name if patient else "Unknown patient",
        date=appointment.appointment_date,
        time=appointment.appointment_time,
        status=appointment.status,
        confirmation_email_sent=confirmation_email_sent,
    ).model_dump(mode="json")


@router.get("/appointments/mine", response_model=list[AppointmentResponse])
def get_my_appointments(patient: Patient = Depends(require_patient), db: Session = Depends(get_db)):
    appointments = db.scalars(select(Appointment).where(
        Appointment.patient_id == patient.id,
    ).order_by(Appointment.appointment_date.desc(), Appointment.appointment_time)).all()
    return [appointment_data(item, db) for item in appointments]


@router.get("/appointments", response_model=list[AppointmentResponse])
def get_clinic_appointments(
    _: Principal = Depends(require_clinic),
    db: Session = Depends(get_db),
):
    appointments = db.scalars(select(Appointment).order_by(
        Appointment.appointment_date, Appointment.appointment_time,
    )).all()
    return [appointment_data(item, db) for item in appointments]


@router.post("/appointments", response_model=AppointmentResponse, status_code=201)
def create_appointment(
    payload: AppointmentCreate,
    patient: Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    clinic = db.get(Clinic, payload.clinic_id)
    if clinic is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    if payload.time.strftime("%H:%M") not in available_slots(clinic, payload.date, db):
        raise HTTPException(status_code=409, detail="That appointment slot is unavailable")
    appointment = Appointment(
        reference=f"RG-{uuid4().hex[:10].upper()}",
        patient_id=patient.id,
        clinic_id=clinic.id,
        appointment_date=payload.date,
        appointment_time=payload.time,
    )
    db.add(appointment)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="That appointment slot was just booked") from error
    db.refresh(appointment)
    email_sent = False
    try:
        send_email(
            recipient=patient.email,
            subject="Your RetinaGuard demo appointment",
            body=(
                f"Hello {patient.name},\n\n"
                "Your RetinaGuard demo appointment has been saved.\n\n"
                f"Clinic: {clinic.name}\n"
                f"Date: {appointment.appointment_date:%B} {appointment.appointment_date.day}, "
                f"{appointment.appointment_date.year}\n"
                f"Time: {appointment.appointment_time:%H:%M} Africa/Cairo\n"
                f"Reference: {appointment.reference}\n\n"
                "This is an illustrative demo booking. The clinic has not been contacted, "
                "and this email is not confirmation of a real clinic reservation.\n\n"
                "RetinaGuard"
            ),
        )
        email_sent = True
    except Exception:
        logger.exception("Failed to send appointment confirmation email")
    return appointment_data(appointment, db, confirmation_email_sent=email_sent)


@router.patch("/appointments/{reference}/cancel", response_model=AppointmentResponse)
def cancel_appointment(
    reference: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    query = select(Appointment).where(Appointment.reference == reference)
    if principal.role == "patient":
        query = query.where(Appointment.patient_id == int(principal.subject))
    appointment = db.scalar(query)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if appointment.status == "Cancelled":
        return appointment_data(appointment, db)
    appointment.status = "Cancelled"
    db.commit()
    db.refresh(appointment)
    return appointment_data(appointment, db)