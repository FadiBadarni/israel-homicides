"""Storage sub-package: SQLAlchemy engine, session factory, and repository functions."""

from crime_pipeline.storage.db import get_engine, get_session, init_db
from crime_pipeline.storage.repository import (
    get_all_extractions,
    get_articles_by_status,
    get_canonical_cases_by_run,
    get_extractions_for_article,
    save_article,
    save_canonical_case,
    save_extraction,
)

__all__ = [
    "get_engine",
    "get_session",
    "init_db",
    "save_article",
    "get_articles_by_status",
    "save_extraction",
    "get_all_extractions",
    "get_extractions_for_article",
    "save_canonical_case",
    "get_canonical_cases_by_run",
]
