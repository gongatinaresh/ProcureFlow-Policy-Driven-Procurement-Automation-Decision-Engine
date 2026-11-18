from sqlalchemy import Engine

from app.database.base import Base
from app.database.session import engine as default_engine
from app import models  # noqa: F401


def initialize_database(bind: Engine | None = None) -> None:
    Base.metadata.create_all(bind=bind or default_engine)
