"""FastAPI dependency injection helpers."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from glokta.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield a database session; close on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()