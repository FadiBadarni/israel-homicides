"""Tests for the B-stage URL-path-segment blocklist in arab48.py.

The blocklist drops articles whose first URL path segment is a stable
editorial category that never carries homicide news (sports, culture,
tech, opinion). This is the pre-fetch precision win from the
search-noise debate (synthesis: option G).
"""
from __future__ import annotations

from urllib.parse import urlparse

from crime_pipeline.scrapers.arab48 import _is_non_homicide_path


def _path(url: str) -> str:
    return urlparse(url).path


# ---------------------------------------------------------------------------
# Drops — known non-homicide editorial categories
# ---------------------------------------------------------------------------

def test_drops_sports() -> None:
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/رياضة/رياضة-عالمية/2026/05/10/title")
    ) is True


def test_drops_culture() -> None:
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/ثقافة-وفنون/حدث/2026/05/10/title")
    ) is True


def test_drops_tech() -> None:
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/علوم-وتكنولوجيا/أخبار-التكنولوجيا/2026/05/10/title")
    ) is True


def test_drops_opinion() -> None:
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/مقالات-وآراء/رأي/2026/05/10/title")
    ) is True


# ---------------------------------------------------------------------------
# Keeps — sections where homicides actually appear
# ---------------------------------------------------------------------------

def test_keeps_local_news() -> None:
    """The Magdi Atef Shela'ata homicide URL we verified end-to-end."""
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/محليات/أخبار-محلية/2026/03/10/title")
    ) is False


def test_keeps_breaking_news() -> None:
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/الأخبار/أخبار-عاجلة/2026/05/10/title")
    ) is False


def test_keeps_israeli_affairs() -> None:
    """May contain Arab-society homicides covered from Israeli political angle."""
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/إسرائيليات/أخبار/2026/05/10/title")
    ) is False


def test_keeps_arab_world_news() -> None:
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/أخبار-عربية-ودولية/أخبار/2026/05/10/title")
    ) is False


def test_keeps_video() -> None:
    """Codex flagged video as KEEP — crime reports may live there."""
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/فيديو/فيديو/2026/05/10/title")
    ) is False


def test_keeps_local_studies_reports() -> None:
    """May carry crime analysis; manual policy decision per Codex."""
    assert _is_non_homicide_path(
        _path("https://www.arab48.com/محليات/دراسات-وتقارير/2026/05/10/title")
    ) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_path_does_not_crash() -> None:
    assert _is_non_homicide_path("/") is False


def test_root_path_does_not_crash() -> None:
    assert _is_non_homicide_path("") is False


def test_path_with_only_blocklisted_segment_is_dropped() -> None:
    """Pathological short URL like /رياضة still classified."""
    assert _is_non_homicide_path("/رياضة") is True
