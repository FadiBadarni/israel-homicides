"""
SQLAlchemy engine and session factory initialisation.
"""
from __future__ import annotations

from typing import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from crime_pipeline.models import Base

# Module-level session factory; populated by init_db().
SessionLocal: sessionmaker[Session] | None = None


def _enable_wal_mode(dbapi_connection: object, _connection_record: object) -> None:
    """Enable WAL journal mode for better concurrent read performance on SQLite."""
    cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(db_path: str) -> Engine:
    """
    Create (or reuse) the SQLAlchemy engine, apply SQLite pragmas, and
    ensure all ORM tables exist.
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    event.listen(engine, "connect", _enable_wal_mode)
    Base.metadata.create_all(engine)
    _apply_additive_migrations(engine)
    return engine


def _apply_additive_migrations(engine: Engine) -> None:
    """Add new columns to existing tables when the model evolves.

    SQLAlchemy's ``create_all`` only creates *missing tables*, never adds
    columns to tables that already exist. For purely-additive changes
    (new nullable columns) we ALTER TABLE inline so existing SQLite DBs
    keep working without a separate Alembic migration step.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "raw_articles" not in insp.get_table_names():
        return  # fresh DB — create_all just made it with all columns

    existing_cols = {c["name"] for c in insp.get_columns("raw_articles")}
    additive_cols = [
        # Triage stage metadata (added when triage was introduced)
        ("triage_status", "VARCHAR(8)"),
        ("triage_incident_type", "VARCHAR(32)"),
        ("triage_reason", "VARCHAR(64)"),
        ("triage_model_version", "VARCHAR(64)"),
        ("triage_input_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("triage_output_tokens", "INTEGER NOT NULL DEFAULT 0"),
        # Run scoping (added for the --cities multi-run backfill flow so
        # resume-from-dedup runs only see the current run's articles).
        ("pipeline_run_id", "VARCHAR(64)"),
    ]
    with engine.begin() as conn:
        for col_name, col_def in additive_cols:
            if col_name not in existing_cols:
                conn.execute(
                    text(f"ALTER TABLE raw_articles ADD COLUMN {col_name} {col_def}")
                )
        # Index on pipeline_run_id for efficient run-scoped lookups
        if "pipeline_run_id" in existing_cols or "pipeline_run_id" in [
            c[0] for c in additive_cols
        ]:
            try:
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS "
                        "ix_raw_articles_pipeline_run_id ON raw_articles(pipeline_run_id)"
                    )
                )
            except Exception:  # pragma: no cover — index already exists or DB-engine quirk
                pass


def init_db(db_path: str) -> Engine:
    """
    Initialise the module-level ``SessionLocal`` factory and return the engine.

    Call this once at application startup before using ``get_session()``.
    """
    global SessionLocal
    engine = get_engine(db_path)
    # expire_on_commit=False so ORM instances remain usable after the session
    # closes (we frequently commit-and-detach for cross-stage data passing).
    SessionLocal = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )
    return engine


def get_session() -> Generator[Session, None, None]:
    """
    Yield a database session and ensure it is closed afterwards.

    Usage::

        with get_session() as session:
            ...

    Raises ``RuntimeError`` if ``init_db()`` has not been called yet.
    """
    if SessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
