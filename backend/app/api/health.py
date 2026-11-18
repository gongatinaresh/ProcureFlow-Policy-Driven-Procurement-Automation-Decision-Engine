from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database.session import get_db
from app.schemas.health import HealthResponse
from app.services.health import check_database

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check(
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    check_database(session)
    return HealthResponse(status="ok", database="ok", service=settings.app_name)
