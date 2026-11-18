import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database.init_db import initialize_database
from app.database.session import create_database_engine, get_db
from app.main import create_app


@pytest.fixture
def test_engine() -> Engine:
    engine = create_database_engine("sqlite://")
    initialize_database(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def test_session_factory(test_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=test_engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.fixture
def test_client(test_session_factory: sessionmaker[Session]) -> TestClient:
    settings = Settings(
    auto_create_tables=False,
    cors_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
)
    application = create_app(settings)

    def override_get_db():
        with test_session_factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as client:
        yield client
    application.dependency_overrides.clear()
