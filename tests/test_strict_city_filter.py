"""Tests for the --strict-city post-merge filter (S5).

Per Sonnet's discovery rules:
- KEEP and tag with `city_filter_unverified` when the gazetteer can't
  validate (no city extracted, or unknown to the gazetteer)
- DROP only on a confirmed mismatch
- The filter slot is post-merge so we use the canonical city, not raw LLM
"""
from __future__ import annotations

import inspect
import types

from crime_pipeline.pipeline import Pipeline


def _stub_pipeline_with_target(name_en: str = "Arraba") -> Pipeline:
    """Pipeline-shaped object with strict_city wired up + a known target."""
    p = types.SimpleNamespace(
        strict_city=True,
        _strict_city_target={
            "name_ar": "عرابة",
            "name_he": "עראבה",
            "name_en": name_en,
        },
        stats={},
        _matches_strict_city=Pipeline._matches_strict_city,
    )
    return p


def _case(victim: str | None = "X", city: str | None = "عرابة") -> object:
    return types.SimpleNamespace(
        canonical_case_id=f"test-{victim}",
        city=city,
        victim_name=victim,
        flags=[],
    )


# ---------------------------------------------------------------------------
# Match cases (KEEP)
# ---------------------------------------------------------------------------

def test_keeps_matching_arabic_city() -> None:
    p = _stub_pipeline_with_target("Arraba")
    case = _case(city="عرابة")
    keep, reason = p._matches_strict_city(p, case)
    assert keep is True
    assert "match" in reason


def test_keeps_matching_hebrew_city() -> None:
    p = _stub_pipeline_with_target("Arraba")
    case = _case(city="עראבה")
    keep, reason = p._matches_strict_city(p, case)
    assert keep is True


def test_keeps_matching_english_city() -> None:
    p = _stub_pipeline_with_target("Arraba")
    case = _case(city="Arraba")
    keep, reason = p._matches_strict_city(p, case)
    assert keep is True


# ---------------------------------------------------------------------------
# Mismatch (DROP)
# ---------------------------------------------------------------------------

def test_drops_confirmed_mismatch() -> None:
    """Tel Aviv case in an Arraba run — drop."""
    p = _stub_pipeline_with_target("Arraba")
    case = _case(city="תל אביב")
    keep, reason = p._matches_strict_city(p, case)
    assert keep is False
    assert "mismatch" in reason


# ---------------------------------------------------------------------------
# Gazetteer-unknown / no city (KEEP + flag — never silently drop)
# ---------------------------------------------------------------------------

def test_keeps_and_flags_when_no_city_extracted() -> None:
    """Sonnet's rule: never silently drop on missing data."""
    p = _stub_pipeline_with_target("Arraba")
    case = _case(city=None)
    keep, reason = p._matches_strict_city(p, case)
    assert keep is True
    assert "city_filter_unverified" in case.flags
    assert reason == "no_city_extracted"


def test_keeps_and_flags_when_gazetteer_misses() -> None:
    """An obscure village not in the gazetteer must survive with a flag."""
    p = _stub_pipeline_with_target("Arraba")
    case = _case(city="ZZZ-Made-Up-Village-123")
    keep, reason = p._matches_strict_city(p, case)
    assert keep is True
    assert "city_filter_unverified" in case.flags


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def test_cli_exposes_strict_city_flag() -> None:
    from crime_pipeline.__main__ import cli
    src = inspect.getsource(cli.callback)
    assert "strict_city" in src

    import crime_pipeline.__main__ as cli_mod
    mod_src = inspect.getsource(cli_mod)
    assert '"--strict-city"' in mod_src


def test_pipeline_constructor_accepts_strict_city() -> None:
    sig = inspect.signature(Pipeline.__init__)
    assert "strict_city" in sig.parameters
    assert sig.parameters["strict_city"].default is False
