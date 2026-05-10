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
    return engine


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
