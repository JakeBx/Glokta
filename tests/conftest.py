"""Shared pytest fixtures for Glokta."""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

# Set testing flag BEFORE importing app to prevent DB connection at import time
os.environ["TESTING"] = "1"

from glokta.database import Base
from glokta.api.app import create_app
from glokta.api.deps import get_db


@pytest.fixture(scope="session")
def engine():
    """In-memory SQLite engine for unit tests — fast and isolated."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(engine):
    """Transactional session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    SessionFactory = sessionmaker(bind=connection)
    session = SessionFactory()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def api_client(db_session: Session):
    """FastAPI test client with DB dependency overridden to use test session."""
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client