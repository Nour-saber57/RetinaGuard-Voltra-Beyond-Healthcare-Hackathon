from io import BytesIO
import logging
import os
from pathlib import Path
from uuid import uuid4
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from email_service import send_email
from models import Case, Patient
from schemas import CaseCreate, CaseResponse, DiabetesType
from security import Principal, current_principal, require_clinic, require_patient

router = APIRouter(prefix="/cases", tags=["Cases"])
logger = logging.getLogger(__name__)
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png"}


@router.get("", response_model=list[CaseResponse])
def get_cases(_: Principal = Depends(require_clinic), db: Session = Depends(get_db)):
    return db.scalars(select(Case).order_by(Case.id.desc())).all()


@router.get("/mine", response_model=list[CaseResponse])
def get_my_cases(patient: Patient = Depends(require_patient), db: Session = Depends(get_db)):
    return db.scalars(
        select(Case).where(Case.patient_id == patient.id).order_by(Case.id.desc())
    ).all()


@router.get("/{case_id}", response_model=CaseResponse)
def get_case(
    case_id: int,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    if principal.role == "patient" and case.patient_id != int(principal.subject):
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def validate_image(image: UploadFile) -> tuple[bytes, str]:
    if image.size is not None and image.size > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image exceeds the 10 MB limit")

    contents = image.file.read(MAX_IMAGE_SIZE + 1)
    if len(contents) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image exceeds the 10 MB limit")
    if not contents:
        raise HTTPException(status_code=400, detail="Image file is empty")

    try:
        with Image.open(BytesIO(contents)) as decoded:
            image_format = decoded.format
            if image_format not in ALLOWED_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=415,
                    detail="Only JPEG and PNG images are supported",
                )
            if decoded.width * decoded.height > MAX_IMAGE_PIXELS:
                raise HTTPException(
                    status_code=413,
                    detail="Image exceeds the 40 megapixel limit",
                )
            decoded.verify()

        with Image.open(BytesIO(contents)) as decoded:
            decoded.load()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError, UnidentifiedImageError) as error:
        raise HTTPException(status_code=400, detail="Image is damaged or invalid") from error

    return contents, ALLOWED_IMAGE_FORMATS[image_format]


@router.post("", response_model=CaseResponse, status_code=201)
def create_case(
    image: Annotated[UploadFile, File()],
    principal: Principal = Depends(current_principal),
    patient_id: Annotated[int | None, Form()] = None,
    name: Annotated[str | None, Form(min_length=1, max_length=100)] = None,
    age: Annotated[int | None, Form(ge=0, le=120)] = None,
    email: Annotated[EmailStr | None, Form(max_length=254)] = None,
    diabetes_type: Annotated[DiabetesType | None, Form()] = None,
    db: Session = Depends(get_db),
):
    patient = principal.patient if principal.role == "patient" else db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=422, detail="Select a valid patient for this screening")
    payload = CaseCreate(
        name=patient.name,
        age=age,
        email=patient.email,
        diabetes_type=patient.diabetes_type,
    )
    image_contents, image_extension = validate_image(image)
    upload_path: Path | None = None

    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid4().hex}{image_extension}"
        upload_path = UPLOAD_DIR / filename
        with upload_path.open("xb") as stored_image:
            stored_image.write(image_contents)

        case = Case(
            name=payload.name,
            email=str(payload.email),
            age=payload.age,
            diabetes_type=payload.diabetes_type.value,
            image_path=(Path("uploads") / filename).as_posix(),
            status="image_uploaded",
            prediction=None,
            patient_id=patient.id,
        )

        db.add(case)
        db.flush()
        db.refresh(case)
        db.commit()
    except Exception:
        db.rollback()
        if upload_path is not None:
            try:
                upload_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove image after case persistence failed")
        raise

    if os.getenv("SEND_CASE_EMAILS", "false").lower() in {"1", "true", "yes"}:
        try:
            send_email(
                recipient=case.email,
                subject="We received your RetinaGuard case",
                body=(
                    f"Hi {case.name},\n\n"
                    "We received your case submission. Our team will review it and follow up if needed.\n\n"
                    "Thank you,\nRetinaGuard"
                ),
            )
        except Exception:
            logger.exception("Failed to send case confirmation email")

    return case