"""Tests for the per-article media-harvest cache.

The cache replaces the previous "re-harvest + re-download + re-CLIP-classify
on every build_canonical" behaviour with a one-time-per-article materialised
``raw_articles.media_harvest_json`` payload. Article HTML is immutable
post-publish, so cached output stays valid until ``media_harvest_version``
bumps invalidate it.

These tests pin the contract:

1. ``MediaCandidate`` round-trips losslessly through JSON serialization
   (the cache stores ``model_dump(mode='json')`` lists; reads decode via
   ``MediaCandidate(**d)``). If MediaCandidate gains fields with non-trivial
   serialization, this test catches the silent loss.

2. ``_attach_media`` consumes the cache when it's populated at the current
   version, skipping the network + CLIP work entirely.

3. ``_attach_media`` writes the cache on miss so subsequent rebuilds hit.

4. ``_attach_media`` ignores stale cache versions and re-harvests live.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.pipeline import MEDIA_HARVEST_VERSION


# ---------------------------------------------------------------------------
# 1) Serialization round-trip
# ---------------------------------------------------------------------------

def test_media_candidate_round_trips_through_json() -> None:
    """Every field set during harvest+download+classify survives JSON
    round-trip. Regressions here silently corrupt the cache."""
    original = MediaCandidate(
        source_article_url="https://news.example.com/article",
        source_url="https://cdn.example.com/photo.jpg",
        final_url="https://cdn.example.com/photo.jpg",
        discovery_selector="meta:og:image",
        caption="The late victim",
        alt_text="Victim portrait",
        figcaption="Memorial photo",
        surrounding_text="Family released this photo of the victim.",
        width=800,
        height=600,
        mime_type="image/jpeg",
        size_bytes=123456,
        sha256="abc123def456",
        phash="ffff0000ffff0000",
        clip_embedding=[0.1, 0.2, -0.3, 0.4],
        face_count=1,
        classification="victim_portrait",
        classifier_tier="clip",
        classification_confidence=0.87,
        classification_evidence=["caption_match:victim_name", "clip:0.31"],
        is_stock_photo=False,
        is_evidence=True,
        evidence_reason="caption_match:victim:0",
        download_status="ok",
    )

    payload = original.model_dump(mode="json")
    restored = MediaCandidate(**payload)

    # Every field that drives downstream decisions must come back identical.
    for field in (
        "source_article_url", "source_url", "final_url", "discovery_selector",
        "caption", "alt_text", "figcaption", "surrounding_text",
        "width", "height", "mime_type", "size_bytes",
        "sha256", "phash", "clip_embedding", "face_count",
        "classification", "classifier_tier", "classification_confidence",
        "classification_evidence", "is_stock_photo",
        "is_evidence", "evidence_reason", "download_status",
    ):
        assert getattr(restored, field) == getattr(original, field), (
            f"field {field!r} not preserved through JSON round-trip"
        )


# ---------------------------------------------------------------------------
# 2-4) _attach_media cache behaviour
# ---------------------------------------------------------------------------

def _make_pipeline_with_media_stub() -> tuple[Any, MagicMock]:
    """Construct a Pipeline whose MediaPipeline methods are mocks.

    Returns ``(pipeline, media_pipeline_mock)``. The mock's
    ``harvest_one_article`` is observed by tests to verify cache behaviour.
    """
    from crime_pipeline.pipeline import Pipeline
    from crime_pipeline.config import Settings
    from crime_pipeline.storage.db import init_db
    import tempfile

    tmp_db = Path(tempfile.mkdtemp()) / "cache_test.db"
    init_db(str(tmp_db))

    settings = Settings()
    pipe = Pipeline(settings, run_id="test_cache_run", strict_date=False, run_narration=False)

    media_mock = MagicMock()
    media_mock.classifier = MagicMock()
    media_mock.classifier.reset_case_budget = MagicMock()

    # harvest_one_article returns an empty list by default (verified per test)
    media_mock.harvest_one_article = AsyncMock(return_value=[])
    # finalize returns ([], []) — what canonical media gets attached is
    # not under test here; we're testing the cache code path.
    media_mock.finalize = AsyncMock(return_value=([], []))

    pipe._media_pipeline = media_mock
    pipe._media_settings.enabled = True
    return pipe, media_mock


def _make_case() -> Any:
    """Minimal case object with the fields _attach_media reads."""
    case = MagicMock()
    case.victim_name = "Test Victim"
    case.victim_name_ar = None
    case.victim_name_he = None
    case.victim_name_en = None
    case.aliases = []
    case.suspect_name = None
    case.city = "Arraba"
    case.city_normalized = {}
    case.neighborhood = None
    case.media = []
    case.media_evidence = []
    return case


def _serialized_candidate(article_url: str, source_url: str) -> dict[str, Any]:
    return MediaCandidate(
        source_article_url=article_url,
        source_url=source_url,
        discovery_selector="meta:og:image",
        sha256="cached_sha",
        phash="ffff0000ffff0000",
        classification="victim_portrait",
        classifier_tier="keyword",
        classification_confidence=0.8,
        download_status="ok",
    ).model_dump(mode="json")


def test_attach_media_uses_cache_when_version_current() -> None:
    """Cache hit at current version → harvest_one_article never called."""
    pipe, media_mock = _make_pipeline_with_media_stub()
    case = _make_case()

    cached = [_serialized_candidate(
        "https://news.example.com/a",
        "https://cdn.example.com/p.jpg",
    )]
    cluster_input = [{
        "url": "https://news.example.com/a",
        "raw_html": "<html>old content not needed</html>",
        "article_id": "fake-id-1",
        "media_harvest_json": cached,
        "media_harvest_version": MEDIA_HARVEST_VERSION,
    }]

    asyncio.run(pipe._attach_media(case, cluster_input))

    # The single most important assertion: no live harvest happened.
    media_mock.harvest_one_article.assert_not_called()
    # finalize still ran on the deserialized candidates.
    media_mock.finalize.assert_called_once()
    finalize_args, _ = media_mock.finalize.call_args
    cands_passed = finalize_args[0]
    assert len(cands_passed) == 1
    assert cands_passed[0].sha256 == "cached_sha"
    assert cands_passed[0].classification == "victim_portrait"


def test_attach_media_ignores_stale_cache_version() -> None:
    """Cache at older version → ignored; live harvest runs."""
    pipe, media_mock = _make_pipeline_with_media_stub()
    case = _make_case()

    stale_cached = [_serialized_candidate(
        "https://news.example.com/a",
        "https://cdn.example.com/p.jpg",
    )]
    cluster_input = [{
        "url": "https://news.example.com/a",
        "raw_html": "<html>fresh content</html>",
        "article_id": "fake-id-1",
        "media_harvest_json": stale_cached,
        "media_harvest_version": MEDIA_HARVEST_VERSION - 1,  # one version behind
    }]

    asyncio.run(pipe._attach_media(case, cluster_input))

    # Stale cache means live harvest must run.
    media_mock.harvest_one_article.assert_called_once()


def test_attach_media_skips_when_no_html_and_no_cache() -> None:
    """No html and no cache → article contributes nothing, no errors."""
    pipe, media_mock = _make_pipeline_with_media_stub()
    case = _make_case()

    cluster_input = [{
        "url": "https://news.example.com/a",
        "raw_html": None,
        "article_id": "fake-id-1",
        "media_harvest_json": None,
        "media_harvest_version": None,
    }]

    asyncio.run(pipe._attach_media(case, cluster_input))

    media_mock.harvest_one_article.assert_not_called()
    # finalize doesn't run because all_cands is empty (early return).
    media_mock.finalize.assert_not_called()


def test_attach_media_persists_cache_on_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache miss → live harvest runs + _persist_media_cache invoked with the
    article_id and the serialized candidate list."""
    pipe, media_mock = _make_pipeline_with_media_stub()
    case = _make_case()

    # Return one candidate from the live harvest path so cache_writes is populated.
    live_cand = MediaCandidate(
        source_article_url="https://news.example.com/a",
        source_url="https://cdn.example.com/p.jpg",
        discovery_selector="meta:og:image",
        sha256="live_sha",
        phash="ffff0000ffff0000",
        classification="victim_portrait",
        classifier_tier="keyword",
        classification_confidence=0.8,
        download_status="ok",
    )
    media_mock.harvest_one_article = AsyncMock(return_value=[live_cand])

    persist_calls: list[dict[str, list[dict[str, Any]]]] = []

    def fake_persist(self: Any, writes: dict[str, list[dict[str, Any]]]) -> None:
        persist_calls.append(writes)

    monkeypatch.setattr(
        "crime_pipeline.pipeline.Pipeline._persist_media_cache",
        fake_persist,
    )

    cluster_input = [{
        "url": "https://news.example.com/a",
        "raw_html": "<html>fresh content</html>",
        "article_id": "fake-id-1",
        "media_harvest_json": None,
        "media_harvest_version": None,
    }]

    asyncio.run(pipe._attach_media(case, cluster_input))

    assert len(persist_calls) == 1
    writes = persist_calls[0]
    assert "fake-id-1" in writes
    cached_list = writes["fake-id-1"]
    assert len(cached_list) == 1
    assert cached_list[0]["sha256"] == "live_sha"
    assert cached_list[0]["classification"] == "victim_portrait"
