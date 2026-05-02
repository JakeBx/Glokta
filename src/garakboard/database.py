"""SQLAlchemy engine, session factory, and declarative base."""

import time
import logging
from sqlalchemy import create_engine, exc as sa_exc, text, inspect
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


def _is_duplicate_object(exc: sa_exc.ProgrammingError) -> bool:
    """Return True when the ProgrammingError is a PostgreSQL 'already exists' conflict.

    psycopg2 sets pgcode = '42710' (duplicate_object) for enum types and
    '42P07' (duplicate_table) for tables.  Both are benign when we're just
    trying to ensure the schema is up to date.
    """
    pgcode = getattr(exc.orig, "pgcode", None)
    return pgcode in ("42710", "42P07")


def init_db() -> None:
    """Create all tables and enum types, updating the schema if they don't yet exist.

    Safe to run against:
    - A brand-new empty database      → creates everything from scratch.
    - A fully-initialised database    → all create_all() calls are no-ops.
    - A partially-initialised database → creates missing pieces; silently
      ignores 'duplicate object' errors from PostgreSQL when enum types
      or tables were partially created in a previous attempt.

    Retries up to 5 times with exponential backoff (1s, 2s, 4s, 8s, 16s)
    so the API can survive brief postgres unavailability during container
    orchestration or recovery.
    """
    max_retries = 5
    for attempt in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except sa_exc.ProgrammingError as e:
            # PostgreSQL raises ProgrammingError when a named enum type (e.g.
            # run_status, probe_queue_status) already exists.  This happens on
            # partially-initialised databases.  We roll back, then retry once
            # with individual table-level create so that each table/type is
            # attempted independently.
            if _is_duplicate_object(e):
                log.warning(
                    "init_db: duplicate object during create_all (%s); "
                    "retrying per-table to skip existing types/tables.",
                    e.orig,
                )
                # The failed create_all() left an aborted transaction.
                # Dispose the connection pool so future connections start clean,
                # then create each table individually with checkfirst=True so
                # partial failures on individual objects are isolated and skipped.
                engine.dispose()
                for table in Base.metadata.sorted_tables:
                    try:
                        table.create(bind=engine, checkfirst=True)
                    except sa_exc.ProgrammingError as inner:
                        if _is_duplicate_object(inner):
                            log.debug(
                                "init_db: skipping already-existing object for table %s",
                                table.name,
                            )
                            engine.dispose()  # clear aborted transaction state
                        else:
                            raise
                return
            raise
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


def migrate_db() -> None:
    """Add any columns that exist in the ORM models but are missing from the live DB.

    Uses PostgreSQL's ``ADD COLUMN IF NOT EXISTS`` so it is safe to call
    repeatedly — columns that already exist are silently skipped.

    This handles the common case where the codebase has added new nullable
    columns to an existing model (e.g. community-run metadata on ``runs``)
    without a full migration tool.  It is NOT a replacement for Alembic; it
    only ever adds columns, never drops or alters existing ones.

    Call after ``init_db()`` to bring an older live database up to the
    current schema.
    """
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                # Table doesn't exist yet — init_db() handles creation.
                continue
            existing_cols = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                # Compile the column type to its SQL DDL string for this dialect.
                col_type = col.type.compile(dialect=engine.dialect)
                nullable_clause = "NULL" if col.nullable else "NOT NULL"
                stmt = text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS '
                    f'"{col.name}" {col_type} {nullable_clause}'
                )
                log.info("migrate_db: adding missing column %s.%s", table.name, col.name)
                conn.execute(stmt)