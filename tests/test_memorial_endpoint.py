"""Tests for the /api/memorial endpoint."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crime_pipeline.models import CanonicalCase
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


@pytest.fixture
def memorial_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient backed by an isolated SQLite DB.

    /api/memorial now reads from the ``canonical_cases`` SQL table (one
    row per merged case), so the fixture initialises a tmp DB and
    points the module-level SessionLocal at it. Tests insert rows via
    ``_write_run`` to populate fixture data.
    """
    from ui.api import main as api_main

    tmp_db = tmp_path / "test.db"
    init_db(str(tmp_db))
    # Keep file-based endpoints working for the JSON-file legacy path too.
    monkeypatch.setattr(api_main, "OUTPUT_DIR", tmp_path)
    api_main._load_run_cached.cache_clear()

    # Ensure gazetteer is loaded from the canonical data file
    from crime_pipeline.utils import gazetteer
    gazetteer._index = {}
    gazetteer.load_gazetteer(Path("data/gazetteer.json"))

    return TestClient(api_main.app)


def _write_run(_dir: Path, run_id: str, cases: list[dict]) -> None:
    """Insert canonical_cases rows under a ``canonical_*`` run_id.

    Mirrors the production path: build_canonical writes one row per case
    with pipeline_run_id ``canonical_<from>_<to>``. Tests use this helper
    to populate the snapshot the API reads from.
    """
    # The API filters to pipeline_run_ids starting with ``canonical_``;
    # prefix unconditionally so test fixtures pass through the filter.
    db_run_id = run_id if run_id.startswith("canonical_") else f"canonical_{run_id}"
    assert db_module.SessionLocal is not None
    with db_module.SessionLocal() as session:
        for idx, case in enumerate(cases):
            case = {**case}
            # Generate a stable canonical_case_id if absent so dedup
            # behaves predictably across tests.
            case.setdefault(
                "canonical_case_id",
                f"TEST-{db_run_id}-{idx}-{case.get('victim_name', 'NA')}",
            )
            session.add(
                CanonicalCase(
                    case_json=case,
                    sources_merged=[],
                    confidence_score=case.get("confidence_score", 0.0),
                    flags=[],
                    review_status="auto",
                    pipeline_run_id=db_run_id,
                )
            )
        session.commit()


def test_memorial_only_includes_died_cases(memorial_client: TestClient, tmp_path: Path) -> None:
    """Cases with victim_outcome != 'died' are excluded."""
    _write_run(tmp_path, "run1", [
        {"victim_name": "A", "victim_outcome": "died",      "city": "Arraba",  "incident_date": "2026-04-01"},
        {"victim_name": "B", "victim_outcome": "survived",  "city": "Arraba",  "incident_date": "2026-04-02"},
        {"victim_name": "C", "victim_outcome": "critical",  "city": "Tira",    "incident_date": "2026-04-03"},
        {"victim_name": "D", "victim_outcome": "unknown",   "city": "Tira",    "incident_date": "2026-04-04"},
        {"victim_name": "E", "victim_outcome": "died",      "city": "Tira",    "incident_date": "2026-04-05"},
    ])

    resp = memorial_client.get("/api/memorial")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_deaths"] == 2
    by_city = {loc["city"]: loc for loc in data["localities"]}
    assert "Arraba" in by_city
    assert "Tira" in by_city
    assert by_city["Arraba"]["death_count"] == 1
    assert by_city["Tira"]["death_count"] == 1


def test_memorial_attaches_coords_per_locality(memorial_client: TestClient, tmp_path: Path) -> None:
    """Each locality carries its lat/lng from the gazetteer."""
    _write_run(tmp_path, "run1", [
        {"victim_name": "A", "victim_outcome": "died", "city": "Arraba", "incident_date": "2026-04-01"},
    ])

    resp = memorial_client.get("/api/memorial")
    data = resp.json()
    loc = data["localities"][0]
    assert loc["lat"] == pytest.approx(32.8517, abs=0.001)
    assert loc["lng"] == pytest.approx(35.3361, abs=0.001)


def test_memorial_includes_victim_summary_fields(memorial_client: TestClient, tmp_path: Path) -> None:
    """Each death summary carries the slim case-summary fields."""
    _write_run(tmp_path, "run1", [
        {
            "victim_name": "Alice",
            "victim_name_he": "אליס",
            "victim_name_ar": "أليس",
            "victim_age": 30,
            "victim_outcome": "died",
            "city": "Arraba",
            "incident_date": "2026-04-01",
            "confidence_score": 0.9,
        },
    ])

    resp = memorial_client.get("/api/memorial")
    deaths = resp.json()["localities"][0]["deaths"]
    assert len(deaths) == 1
    d = deaths[0]
    assert d["victim_name"] == "Alice"
    assert d["victim_name_he"] == "אליס"
    assert d["victim_name_ar"] == "أليس"
    assert d["victim_age"] == 30
    assert d["incident_date"] == "2026-04-01"
    assert d["confidence_score"] == 0.9
    assert d["case_index"] == 0
    # All deaths now share the snapshot run_id; the case-detail URL is
    # /cases/canonical/{case_index} regardless of which build window
    # produced the underlying row.
    assert d["run_id"] == "canonical"


def test_memorial_year_filter_inclusive(memorial_client: TestClient, tmp_path: Path) -> None:
    """year_from and year_to are both inclusive."""
    _write_run(tmp_path, "run1", [
        {"victim_name": "A", "victim_outcome": "died", "city": "Arraba", "incident_date": "2024-04-01"},
        {"victim_name": "B", "victim_outcome": "died", "city": "Arraba", "incident_date": "2025-04-01"},
        {"victim_name": "C", "victim_outcome": "died", "city": "Arraba", "incident_date": "2026-04-01"},
    ])

    resp = memorial_client.get("/api/memorial?year_from=2025&year_to=2026")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_deaths"] == 2


def test_memorial_unresolved_count_for_unknown_city(memorial_client: TestClient, tmp_path: Path) -> None:
    """Cases whose city is not in the gazetteer are counted in unresolved_count."""
    _write_run(tmp_path, "run1", [
        {"victim_name": "A", "victim_outcome": "died", "city": "Arraba",         "incident_date": "2026-04-01"},
        {"victim_name": "B", "victim_outcome": "died", "city": "Atlantis",       "incident_date": "2026-04-02"},
        {"victim_name": "C", "victim_outcome": "died", "city": "El Dorado",      "incident_date": "2026-04-03"},
    ])

    resp = memorial_client.get("/api/memorial")
    data = resp.json()
    assert data["total_deaths"] == 1
    assert data["unresolved_count"] == 2


def test_memorial_empty_when_no_runs(memorial_client: TestClient, tmp_path: Path) -> None:
    """With no run files in OUTPUT_DIR, the endpoint returns an empty shell."""
    resp = memorial_client.get("/api/memorial")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_deaths"] == 0
    assert data["unresolved_count"] == 0
    assert data["localities"] == []
    assert data["run_id"] is None
