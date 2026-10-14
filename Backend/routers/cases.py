import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from email_service import send_email
from models import Case
from schemas import CaseCreate, CaseResponse

router = APIRouter(prefix="/cases", tags=["Cases"])
logger = logging.getLogger(__name__)


@router.post("", response_model=CaseResponse, status_code=201)
def create_case(
    payload: CaseCreate,
    db: Session = Depends(get_db),
):
    case = Case(
        name=payload.name,
        email=str(payload.email),
        age=payload.age,
        diabetes_type=payload.diabetes_type.value,
    )

    db.add(case)
    db.commit()
    db.refresh(case)

    try:
        send_email(
            recipient=case.email,
            subject="We received your RetinaFlow case",
            body=(
                f"Hi {case.name},\n\n"
                "We received your case submission. Our team will review it and follow up if needed.\n\n"
                "Thank you,\nRetinaFlow"
            ),
        )
    except Exception:
        logger.exception("Failed to send confirmation email for case %s", case.id)

    return case