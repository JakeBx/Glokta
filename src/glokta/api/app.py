"""FastAPI application factory."""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI

from glokta.api.routers import health, models, runs, leaderboard
from glokta.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    # Skip production DB init in test mode - test fixtures manage their own engine
    if not os.environ.get("TESTING"):
        init_db()
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