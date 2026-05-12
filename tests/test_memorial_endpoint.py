"""Tests for the /api/memorial endpoint."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def memorial_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient with OUTPUT_DIR pointed at an isolated tmp directory.

    Also ensures the gazetteer is loaded from data/gazetteer.json (the real one)
    because the memorial endpoint depends on its coordinates.
    """
    from ui.api import main as api_main

    monkeypatch.setattr(api_main, "OUTPUT_DIR", tmp_path)
    # Bust the lru_cache so each test sees fresh file reads
    api_main._load_run_cached.cache_clear()

    # Ensure gazetteer is loaded from the canonical data file
    from crime_pipeline.utils import gazetteer
    gazetteer._index = {}
    gazetteer.load_gazetteer(Path("data/gazetteer.json"))

    return TestClient(api_main.app)


def _write_run(dir_: Path, run_id: str, cases: list[dict]) -> None:
    payload = {
        "schema_version": "2.0",
        "kind": "crime_pipeline.run",
        "pipeline_run_id": run_id,
        "exported_at": "2026-05-12T00:00:00Z",
        "case_count": len(cases),
        "cases": cases,
    }
    (dir_ / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")


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
    assert d["run_id"] == "run1"


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
