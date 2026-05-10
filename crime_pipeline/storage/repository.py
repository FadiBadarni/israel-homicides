"""
Data access layer (repository pattern) for the crime pipeline.

All functions accept an open SQLAlchemy ``Session`` and are intentionally
free of transaction management so the caller controls commit/rollback.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from crime_pipeline.models import CanonicalCase, ExtractedRecord, RawArticle


# ---------------------------------------------------------------------------
# RawArticle
# ---------------------------------------------------------------------------


def save_article(session: Session, article_data: dict[str, Any]) -> RawArticle:
    """
    Insert or update a RawArticle row keyed on *url*.

    If an article with the same URL already exists its mutable fields are
    updated in-place; otherwise a new row is created.  Returns the
    (potentially refreshed) ORM instance.
    """
    url: str = article_data["url"]

    existing: RawArticle | None = session.scalar(
        select(RawArticle).where(RawArticle.url == url)
    )

    if existing is not None:
        # Update mutable fields only — preserve original id / fetched_at.
        mutable_fields = {
            "final_url",
            "title",
            "published_at",
            "raw_html",
            "article_text",
            "content_type",
            "fetch_status",
            "error_message",
            "language",
        }
        for field in mutable_fields:
            if field in article_data:
                setattr(existing, field, article_data[field])
        session.flush()
        return existing

    article = RawArticle(
        id=article_data.get("id", str(uuid.uuid4())),
        source=article_data["source"],
        url=url,
        final_url=article_data.get("final_url", url),
        language=article_data["language"],
        title=article_data.get("title"),
        published_at=article_data.get("published_at"),
        fetched_at=article_data.get("fetched_at", datetime.now(tz=timezone.utc)),
        raw_html=article_data.get("raw_html", ""),
        article_text=article_data.get("article_text", ""),
        content_type=article_data.get("content_type", "article"),
        fetch_status=article_data.get("fetch_status", "success"),
        error_message=article_data.get("error_message"),
    )
    session.add(article)
    session.flush()
    return article


def get_articles_by_status(session: Session, status: str) -> list[RawArticle]:
    """Return all RawArticle rows with the given ``fetch_status``."""
    return list(
        session.scalars(
            select(RawArticle)
            .where(RawArticle.fetch_status == status)
            .order_by(RawArticle.fetched_at.asc())
        )
    )


# ---------------------------------------------------------------------------
# ExtractedRecord
# ---------------------------------------------------------------------------


def save_extraction(
    session: Session, article_id: str, data: dict[str, Any]
) -> ExtractedRecord:
    """
    Persist an LLM extraction result for the given article.

    A new ExtractedRecord is always inserted (articles may be re-extracted
    with different models; historical records are preserved).
    """
    record = ExtractedRecord(
        id=data.get("id", str(uuid.uuid4())),
        article_id=article_id,
        extracted_json=data["extracted_json"],
        validation_status=data.get("validation_status", "valid"),
        llm_model=data["llm_model"],
        input_tokens=data.get("input_tokens", 0),
        output_tokens=data.get("output_tokens", 0),
        cache_hit=data.get("cache_hit", False),
        latency_ms=data.get("latency_ms", 0),
        extracted_at=data.get("extracted_at", datetime.now(tz=timezone.utc)),
        extraction_status=data.get("extraction_status", "success"),
    )
    session.add(record)
    session.flush()
    return record


def get_all_extractions(session: Session) -> list[ExtractedRecord]:
    """Return all ExtractedRecord rows ordered by extraction time ascending."""
    return list(
        session.scalars(
            select(ExtractedRecord).order_by(ExtractedRecord.extracted_at.asc())
        )
    )


def get_extractions_for_article(session: Session, article_id: str) -> list[ExtractedRecord]:
    """Return all extraction records for a specific article."""
    return list(
        session.scalars(
            select(ExtractedRecord)
            .where(ExtractedRecord.article_id == article_id)
            .order_by(ExtractedRecord.extracted_at.desc())
        )
    )


# ---------------------------------------------------------------------------
# CanonicalCase
# ---------------------------------------------------------------------------


def save_canonical_case(session: Session, case_data: dict[str, Any]) -> CanonicalCase:
    """
    Insert a new CanonicalCase row from a dict produced by the Merger stage.

    If a ``pipeline_run_id`` + ``id`` collision occurs the existing record is
    updated; otherwise a fresh row is created.
    """
    case_id: str = case_data.get("id", str(uuid.uuid4()))

    existing: CanonicalCase | None = session.get(CanonicalCase, case_id)
    if existing is not None:
        existing.case_json = case_data["case_json"]
        existing.sources_merged = case_data.get("sources_merged", [])
        existing.confidence_score = case_data.get("confidence_score", 0.0)
        existing.flags = case_data.get("flags", [])
        existing.review_status = case_data.get("review_status", "auto")
        existing.updated_at = datetime.now(tz=timezone.utc)
        session.flush()
        return existing

    now = datetime.now(tz=timezone.utc)
    case = CanonicalCase(
        id=case_id,
        case_json=case_data["case_json"],
        sources_merged=case_data.get("sources_merged", []),
        confidence_score=case_data.get("confidence_score", 0.0),
        flags=case_data.get("flags", []),
        review_status=case_data.get("review_status", "auto"),
        created_at=case_data.get("created_at", now),
        updated_at=case_data.get("updated_at", now),
        pipeline_run_id=case_data.get("pipeline_run_id", ""),
    )
    session.add(case)
    session.flush()
    return case


def get_canonical_cases_by_run(
    session: Session, pipeline_run_id: str
) -> list[CanonicalCase]:
    """Return all CanonicalCase rows for a given pipeline run."""
    return list(
        session.scalars(
            select(CanonicalCase)
            .where(CanonicalCase.pipeline_run_id == pipeline_run_id)
            .order_by(CanonicalCase.created_at.asc())
        )
    )
