"""
City/locality gazetteer for normalising place names extracted from Arabic and
Hebrew news articles.

The gazetteer is loaded lazily from ``data/gazetteer.json`` relative to the
current working directory (or the path supplied to ``load_gazetteer()``).

Expected JSON schema::

    [
        {
            "name_ar": "أم الفحم",
            "name_he": "אום אל-פאחם",
            "name_en": "Umm al-Fahm",
            "district": "Haifa",
            "aliases_ar": ["ام الفحم"],
            "aliases_he": []
        },
        ...
    ]
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import TypedDict

_DEFAULT_GAZETTEER_PATH = Path("data/gazetteer.json")

# In-memory index built by load_gazetteer()
_index: dict[str, CityRecord] = {}


class CityRecord(TypedDict, total=False):
    name_ar: str
    name_he: str
    name_en: str
    district: str
    region: str
    lat: float | None
    lng: float | None


def _normalise_key(raw: str) -> str:
    """
    Normalise a city name string to a lookup key:
    - Unicode NFC normalisation
    - Strip leading/trailing whitespace
    - Collapse internal whitespace
    - Lowercase (for Latin / Hebrew scripts; Arabic is case-invariant)
    """
    normalised = unicodedata.normalize("NFC", raw.strip())
    return " ".join(normalised.lower().split())


def load_gazetteer(path: Path = _DEFAULT_GAZETTEER_PATH) -> None:
    """
    Load the gazetteer JSON file and build the in-memory lookup index.

    Safe to call multiple times; subsequent calls rebuild the index from
    the (potentially updated) file.
    """
    global _index
    _index = {}

    if not path.exists():
        # Create an empty gazetteer file so the path is valid for future use.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]", encoding="utf-8")
        return

    raw: list[dict[str, str | list[str]]] = json.loads(path.read_text(encoding="utf-8"))

    for entry in raw:
        record: CityRecord = {
            "name_ar": str(entry.get("name_ar", "")),
            "name_he": str(entry.get("name_he", "")),
            "name_en": str(entry.get("name_en", "")),
            "district": str(entry.get("district", "")),
            "lat": float(entry["lat"]) if entry.get("lat") is not None else None,
            "lng": float(entry["lng"]) if entry.get("lng") is not None else None,
        }
        if entry.get("region"):
            record["region"] = str(entry["region"])  # type: ignore[typeddict-unknown-key]

        # Index primary names
        for name in (record["name_ar"], record["name_he"], record["name_en"]):
            if name:
                _index[_normalise_key(name)] = record

        # Index aliases (Arabic / Hebrew / English)
        for key in ("aliases_ar", "aliases_he", "aliases_en"):
            for alias in entry.get(key, []) or []:
                _index[_normalise_key(str(alias))] = record


def normalize_city(raw: str) -> CityRecord | None:
    """
    Look up *raw* in the gazetteer and return the canonical city record, or
    ``None`` if no match is found.

    The index is populated lazily on first call.

    Args:
        raw: A city name in any supported language (Arabic, Hebrew, English)
             or a known alias.

    Returns:
        A ``CityRecord`` dict with keys ``name_ar``, ``name_he``, ``name_en``,
        ``district``, and optional ``region``, ``lat``, ``lng``, or ``None``
        if the name is not in the gazetteer.
    """
    if not _index:
        load_gazetteer()

    key = _normalise_key(raw)
    return _index.get(key)


def list_all_cities() -> list[CityRecord]:
    """Return deduplicated list of all city records in the gazetteer."""
    if not _index:
        load_gazetteer()
    seen: set[str] = set()
    result: list[CityRecord] = []
    for record in _index.values():
        key = record["name_en"] or record["name_ar"]
        if key not in seen:
            seen.add(key)
            result.append(record)  # type: ignore[arg-type]
    return result
