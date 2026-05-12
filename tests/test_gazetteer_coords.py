"""Tests for gazetteer coordinate fields (lat/lng on CityRecord)."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from crime_pipeline.utils import gazetteer


@pytest.fixture(autouse=True)
def _reset_index() -> Iterator[None]:
    """Reset the gazetteer index between tests so each test sees a clean load."""
    gazetteer._index = {}
    yield
    # Clear after each test so downstream tests trigger a fresh load from the
    # real data/gazetteer.json (avoids polluting other tests with tmp_path data).
    gazetteer._index = {}


def test_city_record_has_lat_lng_when_present(tmp_path: Path) -> None:
    """When a gazetteer entry has lat/lng, normalize_city returns them on the record."""
    data = [
        {
            "name_en": "Arraba",
            "name_ar": "عرابة",
            "name_he": "עראבה",
            "district": "Northern",
            "lat": 32.8517,
            "lng": 35.3361,
        }
    ]
    f = tmp_path / "gaz.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    gazetteer.load_gazetteer(f)

    rec = gazetteer.normalize_city("Arraba")
    assert rec is not None
    assert rec["lat"] == 32.8517
    assert rec["lng"] == 35.3361


def test_city_record_lat_lng_are_none_when_absent(tmp_path: Path) -> None:
    """Backwards-compat: entries without lat/lng resolve with lat=None, lng=None."""
    data = [
        {
            "name_en": "Nowhere",
            "name_ar": "",
            "name_he": "",
            "district": "",
        }
    ]
    f = tmp_path / "gaz.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    gazetteer.load_gazetteer(f)

    rec = gazetteer.normalize_city("Nowhere")
    assert rec is not None
    assert rec.get("lat") is None
    assert rec.get("lng") is None
