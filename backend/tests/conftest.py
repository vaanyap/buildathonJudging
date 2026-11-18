import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://judging:judging@localhost:5432/judging_test"
os.environ["SEED_ON_START"] = "false"
os.environ["JWT_SECRET"] = "test-secret"

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.main import app
from app.seed import seed_criteria

if "judging_test" not in settings.database_url or settings.seed_on_start:
    raise RuntimeError(f"Refusing to test against {settings.database_url} seed={settings.seed_on_start}")


@pytest.fixture(autouse=True)
def clean_db():
    engine.dispose()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_criteria(db)
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
