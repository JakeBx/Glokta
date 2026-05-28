"""FastAPI application factory."""

from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI

from glokta.api.routers import health, models, runs, leaderboard
from glokta.config import settings
from glokta.database import init_db

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup; seed from HF Dataset in HF Space mode."""
    if not os.environ.get("TESTING"):
        init_db()
        if settings.hf_space_mode:
            try:
                from glokta.hf_sync import import_all
                log.info("HF Space mode: seeding database from HF Dataset...")
                import_all()
                log.info("HF Space mode: seed complete")
            except Exception as exc:
                log.warning("HF Space mode: dataset seed failed (%s) — continuing with empty DB", exc)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Glokta API",
        description="Open LLM Security Leaderboard API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(leaderboard.router, prefix="/api")

    return app


app = create_app()