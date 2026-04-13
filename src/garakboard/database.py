"""SQLAlchemy engine, session factory, and declarative base."""

import time
import logging
from sqlalchemy import create_engine, exc as sa_exc
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from garakboard.config import settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Yield a database session; close on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables with retry-on-startup resilience.

    Retries up to 5 times with exponential backoff (1s, 2s, 4s, 8s, 16s)
    so the API can survive brief postgres unavailability during container
    orchestration or recovery.
    """
    max_retries = 5
    for attempt in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except sa_exc.OperationalError as e:
            if attempt == max_retries - 1:
                log.error("init_db: all retries exhausted, last error: %s", e)
                raise
            wait = 2**attempt
            log.warning(
                "init_db: attempt %d/%d failed (%s). Retrying in %ds.",
                attempt + 1,
                max_retries,
                e,
                wait,
            )
            time.sleep(wait)