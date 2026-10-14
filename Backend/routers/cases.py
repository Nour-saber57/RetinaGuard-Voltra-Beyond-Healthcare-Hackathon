from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from Backend.database import get_db
from Backend.models import Case
from Backend.schemas import CaseCreate, CaseResponse

router = APIRouter(prefix="/cases", tags=["Cases"])


@router.post("", response_model=CaseResponse, status_code=201)
def create_case(
    payload: CaseCreate,
    db: Session = Depends(get_db),
):
    case = Case(
        name=payload.name,
        email=str(payload.email),
    )

    db.add(case)
    db.commit()
    db.refresh(case)

    return case