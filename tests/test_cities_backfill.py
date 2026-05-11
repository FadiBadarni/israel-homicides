"""Tests for the --cities backfill mode (S4).

Per Sonnet's discovery rules:
- --cities and --query are mutually exclusive
- Comma-separated city list (English transliteration)
- Loops once per (city, source) with run_id <city>_<year>_<source>
- Native-script per-source query selection via the gazetteer
- Unknown cities are skipped (warning), not crashed
"""
from __future__ import annotations

import inspect

from click.testing import CliRunner

from crime_pipeline.__main__ import cli


def test_help_lists_cities_flag() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--cities" in result.output


def test_help_lists_cities_year_flag() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "--cities-year" in result.output


def test_query_and_cities_mutually_exclusive() -> None:
    result = CliRunner().invoke(
        cli, ["--query", "x", "--cities", "arraba"]
    )
    # Should error with our explicit message, not crash with TypeError
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output.lower()


def test_neither_query_nor_cities_errors() -> None:
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 2
    assert "required" in result.output.lower()


def test_cli_function_signature_has_cities_params() -> None:
    sig = inspect.signature(cli.callback)
    assert "cities" in sig.parameters
    assert "cities_year" in sig.parameters


def test_cities_dispatch_loop_lives_in_main_module() -> None:
    """Per Sonnet: the loop belongs in __main__.py, not Pipeline.run().
    Source-level guard against accidental refactor that would push this
    into Pipeline (which has stats/run_id init coupling)."""
    import crime_pipeline.__main__ as cli_mod
    src = inspect.getsource(cli_mod)
    # The loop must be visible in the CLI module
    assert "for city_in in city_inputs" in src or "for city_in in" in src
    # Each iteration creates a fresh Pipeline (Sonnet's requirement).
    # The line may wrap to multi-line with strict_city/strict_date kwargs.
    assert "Pipeline(" in src
    assert "run_id=pair_run_id" in src


def test_cities_runs_per_source_with_native_query() -> None:
    """Source-level guard: Arab48 must use Arabic name, Ynet must use Hebrew."""
    import crime_pipeline.__main__ as cli_mod
    src = inspect.getsource(cli_mod)
    assert 'source == "arab48"' in src
    assert 'source == "ynet"' in src
    assert 'name_ar' in src
    assert 'name_he' in src
