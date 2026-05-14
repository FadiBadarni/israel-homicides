"""Tests for the kul-alarab scraper.

Discovery uses the JSON API at ``apiv3.alarab.com`` (the same endpoint
that powers the site's infinite-scroll tag pages). Most tests are
structural — pinning the URL pattern, date parsing, registry wiring.
The two integration tests stub ``AsyncClient.get`` so they exercise
the discover loop end-to-end without network I/O.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from crime_pipeline.scrapers import SCRAPER_REGISTRY, get_scraper
from crime_pipeline.scrapers.kul_alarab import (
    KulAlarabScraper,
    _parse_pdate,
)


# ---------------------------------------------------------------------------
# Structural / registry tests
# ---------------------------------------------------------------------------


def test_kul_alarab_registered_in_registry() -> None:
    """Without the registration the sweep silently can't reach this source."""
    assert "kul_alarab" in SCRAPER_REGISTRY
    assert SCRAPER_REGISTRY["kul_alarab"] is KulAlarabScraper


def test_kul_alarab_basic_attrs() -> None:
    assert KulAlarabScraper.source_name == "kul_alarab"
    assert KulAlarabScraper.language == "ar"
    assert KulAlarabScraper.base_domain == "www.kul-alarab.com"


def test_kul_alarab_in_tier2_registry() -> None:
    """Kul al-Arab is Arabic local press — tier 2 alongside Arab48 / Makan."""
    from crime_pipeline.scrapers.tier_registry import (
        DOMAIN_TO_TIER, TIER_2_DOMAINS,
    )
    assert "kul-alarab.com" in TIER_2_DOMAINS
    assert DOMAIN_TO_TIER["kul-alarab.com"] == 2


def test_get_scraper_constructs_kul_alarab() -> None:
    scraper = get_scraper("kul_alarab")
    assert isinstance(scraper, KulAlarabScraper)


def test_discover_signature() -> None:
    """The pipeline calls discover(query, date_from, date_to, max_results=...)
    on every scraper. Keep the signature stable so a refactor here doesn't
    silently drop the source from the orchestrator."""
    sig = inspect.signature(KulAlarabScraper.discover)
    params = sig.parameters
    for name in ("query", "date_from", "date_to", "max_results"):
        assert name in params, f"missing param: {name}"


def test_api_base_pinned() -> None:
    """The JSON API URL pattern drives every discover() call. If apiv3 moves,
    the scraper must move with it — pin it explicitly so a refactor surfaces
    the breakage instead of silently returning zero results."""
    from crime_pipeline.scrapers import kul_alarab as mod
    assert mod._API_BASE == "https://apiv3.alarab.com/api/search"
    assert mod._SITE_BASE == "https://www.kul-alarab.com"


# ---------------------------------------------------------------------------
# _parse_pdate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("2026-05-14 08:37:19", datetime(2026, 5, 14, 8, 37, 19, tzinfo=timezone.utc)),
    ("2025-08-24 00:00:00", datetime(2025, 8, 24, 0, 0, 0, tzinfo=timezone.utc)),
    ("2011-12-28 14:00", datetime(2011, 12, 28, 14, 0, tzinfo=timezone.utc)),
    ("2024-01-15", datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)),
])
def test_parse_pdate_handles_api_formats(raw: str, expected: datetime) -> None:
    """The API returns ``YYYY-MM-DD HH:MM:SS`` for every row I've seen, but
    we tolerate the shorter variants too since meta-tag fallback uses them."""
    assert _parse_pdate(raw) == expected


def test_parse_pdate_returns_none_on_garbage() -> None:
    assert _parse_pdate("") is None
    assert _parse_pdate("not a date") is None
    assert _parse_pdate(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Discover — stubbed HTTP
# ---------------------------------------------------------------------------


class _StubClient:
    """Minimal AsyncClient stub: serves canned JSON per page index."""

    def __init__(self, pages: dict[int, list[dict]]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    async def __aenter__(self) -> "_StubClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str) -> Any:
        self.calls.append(url)
        # Extract page from the trailing /{page}
        page = int(url.rstrip("/").split("/")[-1])
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"data": self._pages.get(page, [])},
            raise_for_status=lambda: None,
        )


def _item(article_id: str, pdate: str) -> dict:
    return {"ID": article_id, "pdate": pdate, "title": f"art {article_id}",
            "url": f"/Article/{article_id}"}


@pytest.mark.asyncio
async def test_discover_filters_to_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Items newer than ``date_to`` and older than ``date_from`` are dropped;
    only in-window items return."""
    pages = {
        1: [
            _item("1", "2027-03-01 00:00:00"),  # newer than window — skip
            _item("2", "2025-06-15 00:00:00"),  # in window — keep
            _item("3", "2025-04-01 00:00:00"),  # in window — keep
        ],
        2: [],  # end of archive
    }
    stub = _StubClient(pages)
    s = KulAlarabScraper(request_delay=0.0)
    monkeypatch.setattr(s, "_make_client", lambda timeout=20.0: stub)
    out = await s.discover("قتل", "2025-01-01", "2025-12-31", max_results=100)
    assert len(out) == 2
    assert {d.url.split("/Article/")[-1] for d in out} == {"2", "3"}


@pytest.mark.asyncio
async def test_discover_stops_when_page_entirely_older_than_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API is newest-first; once every item on a page is older than
    ``date_from`` we can stop without walking the rest of the archive."""
    pages = {
        1: [_item("a", "2025-06-15 00:00:00")],   # in window
        2: [_item("b", "2020-01-01 00:00:00"),    # all out of window
            _item("c", "2019-01-01 00:00:00")],
        3: [_item("d", "2018-01-01 00:00:00")],   # would be visited if we didn't stop
    }
    stub = _StubClient(pages)
    s = KulAlarabScraper(request_delay=0.0)
    monkeypatch.setattr(s, "_make_client", lambda timeout=20.0: stub)
    out = await s.discover("قتل", "2025-01-01", "2025-12-31", max_results=100)
    assert len(out) == 1
    # We must NOT have called page 3 — the early-exit guards against
    # walking the entire archive on a narrow recent window.
    assert not any(u.endswith("/3") for u in stub.calls), stub.calls


@pytest.mark.asyncio
async def test_discover_stops_on_empty_api_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-of-archive is signaled by an empty ``data`` array. The walk
    must halt — otherwise we'd loop ``max_pages`` times against a
    dead API."""
    pages = {1: [_item("a", "2025-06-15 00:00:00")], 2: []}
    stub = _StubClient(pages)
    s = KulAlarabScraper(request_delay=0.0)
    monkeypatch.setattr(s, "_make_client", lambda timeout=20.0: stub)
    out = await s.discover("قتل", "2025-01-01", "2025-12-31", max_results=100, max_pages=30)
    assert len(out) == 1
    # Page 3 must NOT be called once page 2 was empty.
    assert sum(1 for u in stub.calls if u.endswith("/3")) == 0


@pytest.mark.asyncio
async def test_discover_builds_api_url_with_correct_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Arabic query must be URL-encoded into the path. A regression
    here (e.g. passing the raw Arabic) silently returns 404s and zero
    discoveries."""
    pages = {1: []}
    stub = _StubClient(pages)
    s = KulAlarabScraper(request_delay=0.0)
    monkeypatch.setattr(s, "_make_client", lambda timeout=20.0: stub)
    await s.discover("قتل", "2025-01-01", "2025-12-31", max_results=10)
    assert stub.calls, "expected at least one API call"
    url = stub.calls[0]
    # %D9%82%D8%AA%D9%84 is the URL-encoded form of قتل
    assert "%D9%82%D8%AA%D9%84" in url
    assert url.startswith("https://apiv3.alarab.com/api/search/")
    assert url.endswith("/1")  # first page is page=1, not page=0
