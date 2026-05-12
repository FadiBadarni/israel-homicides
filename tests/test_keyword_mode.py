"""Tests for the --keyword-mode sweep (S2).

Per the synthesis:
- 3 modes: hebrew, arabic, both
- Hebrew keywords routed to Ynet only; Arabic to Arab48 only
- 2 keywords per language (curated, recall-biased)
- Each (keyword, source) pair gets its own run_id
- Mutually exclusive with --query / --cities
"""
from __future__ import annotations

import inspect

from click.testing import CliRunner

from crime_pipeline.__main__ import cli


def test_help_lists_keyword_mode_flag() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--keyword-mode" in result.output


def test_keyword_mode_choices_are_hebrew_arabic_both() -> None:
    """Click should reject unknown values."""
    result = CliRunner().invoke(cli, ["--keyword-mode", "invalid"])
    assert result.exit_code != 0
    # Click reports invalid choice
    assert "invalid" in result.output.lower() or "choice" in result.output.lower()


def test_keyword_mode_with_query_is_rejected() -> None:
    """Mutually exclusive guard."""
    result = CliRunner().invoke(
        cli, ["--keyword-mode", "hebrew", "--query", "x"]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower()


def test_keyword_mode_with_cities_is_rejected() -> None:
    """Mutually exclusive guard."""
    result = CliRunner().invoke(
        cli, ["--keyword-mode", "arabic", "--cities", "arraba"]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower()


def test_curated_hebrew_keywords_present_in_source() -> None:
    """Per Gemini's discover phase: רצח and נרצח are the recall-biased
    Hebrew union. Source-level invariant guards against accidental
    keyword reduction."""
    import crime_pipeline.__main__ as cli_mod
    src = inspect.getsource(cli_mod)
    assert "רצח" in src
    assert "נרצח" in src


def test_curated_arabic_keywords_present_in_source() -> None:
    import crime_pipeline.__main__ as cli_mod
    src = inspect.getsource(cli_mod)
    assert "جريمة قتل" in src
    assert "مقتل" in src


def test_keyword_mode_routes_hebrew_to_ynet_only() -> None:
    """Per the synthesis + later additions: Hebrew kw → Hebrew sites
    (ynet, walla); Arabic kw → Arabic-language sites (arab48, makan).
    Makan was added 2026-05 (Bedouin/Negev coverage gap, تيمور عطالله);
    Walla was added shortly after to close the femicide gap on cases
    that Ynet didn't headline by name (بسمة أبو فريحة)."""
    import crime_pipeline.__main__ as cli_mod
    src = inspect.getsource(cli_mod)
    # Hebrew side must include ynet AND walla in some order.
    assert (
        '"ynet", "walla"' in src
        or '"walla", "ynet"' in src
    )
    # Arabic side must include both Arab48 and Makan in some order.
    assert (
        '"ar": ["arab48", "makan"]' in src
        or '"ar": ["makan", "arab48"]' in src
    )


def test_keyword_mode_dispatch_loop_in_main() -> None:
    """The loop must live in __main__.py (not Pipeline.run())."""
    import crime_pipeline.__main__ as cli_mod
    src = inspect.getsource(cli_mod)
    # Loop body creates fresh Pipeline per (kw, source)
    assert "for kw, source, lang in plan" in src
    assert 'pair_run_id = f"kw_' in src


def test_cli_signature_has_keyword_mode_param() -> None:
    sig = inspect.signature(cli.callback)
    assert "keyword_mode" in sig.parameters
