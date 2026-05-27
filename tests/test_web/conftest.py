import os
import pytest
from fastapi.testclient import TestClient

# Enable metrics before web.main is imported so the /metrics endpoint
# and DBCollector are registered during test runs.
os.environ.setdefault("METRICS_ENABLED", "true")
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from web.database import Base, get_db
import web.models  # registers all models with Base metadata
import web.routers.auth as _auth_router
from web.limiter import limiter as _limiter
from web.main import app

# Tests use plain HTTP — disable the Secure cookie flag and rate limiting
_auth_router.COOKIE_SECURE = False
_limiter.enabled = False

TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="function")
def db_engine():
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(db_engine):
    Session = sessionmaker(bind=db_engine)

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
