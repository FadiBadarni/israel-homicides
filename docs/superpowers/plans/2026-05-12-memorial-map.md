# Memorial Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the case-browser UI with a single-page memorial showing a quiet map of Israel, locality dots for died-outcome victims, recency-weighted pulse, and an inline bloom card for case detail.

**Architecture:** Two-part change. Backend extends the gazetteer with `lat`/`lng` per locality and exposes a new `/api/memorial` endpoint that aggregates died-outcome cases by city. Frontend is rebuilt around a single page with a MapLibre GL JS canvas, self-hosted Protomaps tiles, and three peripheral chrome elements (title, year scrubber, count). The full memorial payload is loaded once; year-range filtering happens client-side.

**Tech Stack:**
- Backend: Python 3.11, FastAPI (existing), pytest (existing).
- Frontend: Next.js 16 App Router, React 19, TypeScript, Tailwind 3, MapLibre GL JS 4.x, `pmtiles` 3.x.
- Tiles: Self-hosted Protomaps `.pmtiles` for Israel region.

**Reference:** See `docs/superpowers/specs/2026-05-12-memorial-map-design.md` for the design spec.

---

## Phase 1 — Backend gazetteer coordinates

### Task 1: Add `lat`/`lng` fields to `CityRecord`

**Files:**
- Modify: `crime_pipeline/utils/gazetteer.py`
- Test: `tests/test_gazetteer_coords.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_gazetteer_coords.py`:

```python
"""Tests for gazetteer coordinate fields (lat/lng on CityRecord)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crime_pipeline.utils import gazetteer


@pytest.fixture(autouse=True)
def _reset_index() -> None:
    """Reset the gazetteer index between tests so each test sees a clean load."""
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gazetteer_coords.py -v`
Expected: FAIL — `KeyError: 'lat'` (TypedDict has no `lat` field) or assertion failure on the second test because `lat` is never written.

- [ ] **Step 3: Update `CityRecord` and `load_gazetteer`**

In `crime_pipeline/utils/gazetteer.py`:

Replace the `CityRecord` TypedDict (around line 35):

```python
class CityRecord(TypedDict, total=False):
    name_ar: str
    name_he: str
    name_en: str
    district: str
    region: str
    lat: float | None
    lng: float | None
```

(The `total=False` makes `region`/`lat`/`lng` optional. Required keys stay required at the type level via the comment; the file's existing `# type: ignore` lines remain valid.)

In `load_gazetteer`, replace the per-entry record construction (around lines 72–82) with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gazetteer_coords.py -v`
Expected: PASS (both tests green).

- [ ] **Step 5: Commit**

```bash
git add crime_pipeline/utils/gazetteer.py tests/test_gazetteer_coords.py
git commit -m "feat(gazetteer): add lat/lng coordinate fields to CityRecord"
```

---

### Task 2: Populate coordinates for all 52 gazetteer entries

**Files:**
- Modify: `data/gazetteer.json`
- Test: `tests/test_gazetteer_coords.py` (extend)

- [ ] **Step 1: Write the failing test (extend existing file)**

Append to `tests/test_gazetteer_coords.py`:

```python
def test_every_gazetteer_entry_has_coords() -> None:
    """Every entry in data/gazetteer.json must have valid lat/lng."""
    data = json.loads(Path("data/gazetteer.json").read_text(encoding="utf-8"))
    missing = [e["name_en"] for e in data if e.get("lat") is None or e.get("lng") is None]
    assert not missing, f"Gazetteer entries missing lat/lng: {missing}"


def test_coords_are_within_israel_bbox() -> None:
    """Sanity check: every coord should be within a generous Israel bounding box.

    Bounds intentionally generous to cover West Bank, Gaza, Golan, and Sinai border.
    """
    data = json.loads(Path("data/gazetteer.json").read_text(encoding="utf-8"))
    out_of_bbox = []
    for e in data:
        lat, lng = e.get("lat"), e.get("lng")
        if lat is None or lng is None:
            continue
        if not (29.0 <= lat <= 34.0) or not (33.8 <= lng <= 36.2):
            out_of_bbox.append((e["name_en"], lat, lng))
    assert not out_of_bbox, f"Coords outside Israel bbox: {out_of_bbox}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gazetteer_coords.py -v`
Expected: FAIL on `test_every_gazetteer_entry_has_coords` listing all 52 cities as missing coords.

- [ ] **Step 3: Run the population script**

Create a one-shot script at `scripts/populate_gazetteer_coords.py`:

```python
"""One-shot: populate lat/lng on data/gazetteer.json from a known coordinate map.

Coordinates are WGS84 decimal degrees, sourced from Wikidata/Wikipedia and
rounded to four decimal places (~11 m precision — more than enough for a
locality marker).
"""
from __future__ import annotations

import json
from pathlib import Path

# Known coordinates for every city currently in data/gazetteer.json.
# Keyed by name_en for stability across renames in non-English fields.
COORDS: dict[str, tuple[float, float]] = {
    "Arraba": (32.8517, 35.3361),
    "Sakhnin": (32.8633, 35.2961),
    "Deir Hanna": (32.8617, 35.3650),
    "Majd al-Krum": (32.9181, 35.2606),
    "Tamra": (32.8533, 35.1981),
    "Shfaram": (32.8056, 35.1700),
    "Kafr Kanna": (32.7444, 35.3417),
    "Nazareth": (32.7019, 35.2978),
    "Kafr Manda": (32.8133, 35.2547),
    "Iksal": (32.6856, 35.3133),
    "Yafa an-Naseriyye": (32.6911, 35.2961),
    "Reineh": (32.7261, 35.3056),
    "Kafr Yasif": (32.9544, 35.1639),
    "Acre": (32.9281, 35.0817),
    "Maghar": (32.8889, 35.4083),
    "Bi'ina": (32.9211, 35.2972),
    "Nahf": (32.9367, 35.3050),
    "Rameh": (32.9381, 35.3678),
    "Kabul": (32.8703, 35.2086),
    "I'billin": (32.8347, 35.1953),
    "Tur'an": (32.7853, 35.3739),
    "Umm al-Fahm": (32.5167, 35.1500),
    "Baqa al-Gharbiyye": (32.4150, 35.0367),
    "Jatt": (32.3950, 35.0517),
    "Ar'ara": (32.4942, 35.1019),
    "Kafr Qara": (32.5067, 35.0833),
    "Haifa": (32.7940, 34.9896),
    "Tira": (32.2333, 34.9500),
    "Taybe": (32.2683, 34.9569),
    "Qalansawe": (32.2858, 34.9839),
    "Lod": (31.9514, 34.8950),
    "Ramla": (31.9292, 34.8669),
    "Jaljulia": (32.1517, 34.9486),
    "Kafr Qasim": (32.1147, 34.9756),
    "Tel Aviv": (32.0853, 34.7818),
    "Jaffa": (32.0508, 34.7503),
    "Jerusalem": (31.7683, 35.2137),
    "Beit Safafa": (31.7458, 35.2086),
    "Abu Ghosh": (31.8064, 35.1075),
    "Rahat": (31.3950, 34.7561),
    "Hura": (31.3083, 34.9389),
    "Tel Sheva": (31.2300, 34.8939),
    "Lakiya": (31.3389, 34.9100),
    "Ksaifa": (31.2767, 34.9947),
    "Beersheba": (31.2517, 34.7917),
    "Kawkab Abu al-Hija": (32.8489, 35.2469),
    "Pardes Hanna-Karkur": (32.4731, 34.9747),
    "Yarka": (32.9658, 35.2125),
    "Arab al-Aramshe": (33.0644, 35.2911),
    "Yanuh-Jat": (32.9989, 35.2186),
    "Fureidis": (32.6133, 34.9389),
    "Shaqib al-Salam": (31.3211, 34.8769),
}


def main() -> None:
    path = Path("data/gazetteer.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    missing: list[str] = []
    for entry in data:
        name = entry["name_en"]
        coords = COORDS.get(name)
        if coords is None:
            missing.append(name)
            continue
        entry["lat"], entry["lng"] = coords
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if missing:
        print(f"WARNING: no coords for: {missing}")
    print(f"Updated {len(data) - len(missing)} entries.")


if __name__ == "__main__":
    main()
```

Run it:

```bash
python scripts/populate_gazetteer_coords.py
```

Expected: `Updated 52 entries.` and no `WARNING` lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gazetteer_coords.py -v`
Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add data/gazetteer.json scripts/populate_gazetteer_coords.py tests/test_gazetteer_coords.py
git commit -m "feat(gazetteer): populate lat/lng for all 52 localities"
```

---

## Phase 2 — Memorial API endpoint

### Task 3: Add `/api/memorial` endpoint (basic shape)

**Files:**
- Modify: `ui/api/main.py`
- Test: `tests/test_memorial_endpoint.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_memorial_endpoint.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memorial_endpoint.py -v`
Expected: All three tests FAIL with 404 (endpoint doesn't exist).

- [ ] **Step 3: Implement `/api/memorial`**

In `ui/api/main.py`, add this import block at the top with the other imports:

```python
from crime_pipeline.utils import gazetteer
```

Add the endpoint near the other `@app.get` endpoints (after `/api/review-pairs`, before `/health`):

```python
@app.get("/api/memorial")
def get_memorial(
    year_from: Optional[int] = Query(None, description="Inclusive lower bound on incident year"),
    year_to: Optional[int] = Query(None, description="Inclusive upper bound on incident year"),
) -> dict:
    """Return the memorial payload: died-outcome cases grouped by locality.

    Only the most recent pipeline run contributes. Cases without a gazetteer
    match are counted in `unresolved_count` and excluded from `localities`.
    """
    runs = _list_runs()
    if not runs:
        return {
            "run_id": None,
            "year_range": {"from": None, "to": None},
            "total_deaths": 0,
            "unresolved_count": 0,
            "localities": [],
        }

    run_path = runs[0]
    data = _load_run(run_path)
    run_id = data.get("pipeline_run_id", run_path.stem)

    # Group died cases by canonical city (via the gazetteer)
    by_city: dict[str, dict] = {}
    unresolved = 0
    incident_years: list[int] = []

    for idx, case in enumerate(data.get("cases", [])):
        if case.get("victim_outcome") != "died":
            continue

        # Year filter
        date = case.get("incident_date")
        year: int | None = None
        if date and len(str(date)) >= 4:
            try:
                year = int(str(date)[:4])
            except ValueError:
                year = None
        if year is not None:
            if year_from is not None and year < year_from:
                continue
            if year_to is not None and year > year_to:
                continue
            incident_years.append(year)

        raw_city = case.get("city")
        rec = gazetteer.normalize_city(raw_city) if raw_city else None
        if not rec or rec.get("lat") is None or rec.get("lng") is None:
            unresolved += 1
            continue

        canonical = rec.get("name_en") or raw_city
        bucket = by_city.setdefault(canonical, {
            "city": canonical,
            "city_he": rec.get("name_he") or None,
            "city_ar": rec.get("name_ar") or None,
            "lat": rec["lat"],
            "lng": rec["lng"],
            "death_count": 0,
            "most_recent_incident_date": None,
            "deaths": [],
        })
        bucket["death_count"] += 1
        if date and (bucket["most_recent_incident_date"] is None or str(date) > bucket["most_recent_incident_date"]):
            bucket["most_recent_incident_date"] = str(date)
        bucket["deaths"].append({
            "case_index": idx,
            "run_id": run_id,
            "victim_name": case.get("victim_name"),
            "victim_name_he": case.get("victim_name_he"),
            "victim_name_ar": case.get("victim_name_ar"),
            "victim_age": case.get("victim_age"),
            "incident_date": str(date) if date else None,
            "confidence_score": case.get("confidence_score"),
        })

    localities = sorted(by_city.values(), key=lambda l: -l["death_count"])
    total_deaths = sum(l["death_count"] for l in localities)

    return {
        "run_id": run_id,
        "year_range": {
            "from": min(incident_years) if incident_years else None,
            "to": max(incident_years) if incident_years else None,
        },
        "total_deaths": total_deaths,
        "unresolved_count": unresolved,
        "localities": localities,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memorial_endpoint.py -v`
Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/api/main.py tests/test_memorial_endpoint.py
git commit -m "feat(api): add /api/memorial endpoint aggregating died cases by locality"
```

---

### Task 4: Memorial endpoint — year filter and unresolved count

**Files:**
- Test: `tests/test_memorial_endpoint.py` (extend)

(Implementation already present from Task 3 — these tests lock it down.)

- [ ] **Step 1: Append the additional tests**

Append to `tests/test_memorial_endpoint.py`:

```python
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
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_memorial_endpoint.py -v`
Expected: all six tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_memorial_endpoint.py
git commit -m "test(api): lock down memorial endpoint year filter and unresolved count"
```

---

## Phase 3 — Frontend teardown

### Task 5: Delete legacy frontend routes and components

**Files:**
- Delete: `ui/frontend/app/cases/` (whole tree)
- Delete: `ui/frontend/app/review/`
- Delete: `ui/frontend/components/cases-table.tsx`
- Delete: `ui/frontend/components/case-filters.tsx`
- Delete: `ui/frontend/components/confidence-badge.tsx`
- Delete: `ui/frontend/components/outcome-badge.tsx`
- Delete: `ui/frontend/components/case-detail.tsx`
- Delete: `ui/frontend/components/media-gallery.tsx`
- Keep (for reuse inside the bloom card): `ui/frontend/components/bidi-name.tsx`
- Modify: `ui/frontend/app/page.tsx` (will be replaced in Task 9; for now make it a stub)
- Modify: `ui/frontend/app/layout.tsx` (remove the navigation header)

- [ ] **Step 1: Delete the legacy files**

```bash
rm -rf ui/frontend/app/cases ui/frontend/app/review
rm ui/frontend/components/cases-table.tsx
rm ui/frontend/components/case-filters.tsx
rm ui/frontend/components/confidence-badge.tsx
rm ui/frontend/components/outcome-badge.tsx
rm ui/frontend/components/case-detail.tsx
rm ui/frontend/components/media-gallery.tsx
```

- [ ] **Step 2: Replace `app/page.tsx` with a placeholder**

```tsx
// ui/frontend/app/page.tsx
export default function HomePage() {
  return <div className="p-6 text-sm text-muted-foreground">Memorial map — under construction.</div>;
}
```

- [ ] **Step 3: Trim `app/layout.tsx` to remove the nav header**

Replace the file with:

```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Crime Pipeline — Memorial",
  description: "A quiet memorial for victims of homicide in Israel",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="he" dir="ltr">
      <body className="min-h-screen bg-background font-sans antialiased">
        {children}
      </body>
    </html>
  );
}
```

- [ ] **Step 4: Verify the project still builds**

Run: `cd ui/frontend && npm run build`
Expected: build succeeds. The page renders the "under construction" stub. (TypeScript may flag unused imports in `lib/api.ts` — those will be replaced in Task 7 and can be ignored here.)

- [ ] **Step 5: Commit**

```bash
git add -A ui/frontend/app ui/frontend/components
git commit -m "chore(ui): delete legacy case-browser routes and components"
```

---

### Task 6: Install MapLibre and pmtiles

**Files:**
- Modify: `ui/frontend/package.json`
- Modify: `ui/frontend/package-lock.json` (auto-generated)

- [ ] **Step 1: Install dependencies**

```bash
cd ui/frontend
npm install maplibre-gl@^4.7.0 pmtiles@^3.2.0
```

- [ ] **Step 2: Verify install**

Inspect `ui/frontend/package.json` — `dependencies` now contains `maplibre-gl` and `pmtiles`.

- [ ] **Step 3: Commit**

```bash
git add ui/frontend/package.json ui/frontend/package-lock.json
git commit -m "chore(ui): add maplibre-gl and pmtiles dependencies"
```

---

### Task 7: Acquire the Protomaps `.pmtiles` file

**Files:**
- Create: `ui/frontend/public/tiles/israel.pmtiles`
- Modify: `.gitignore` (exclude the binary tile file)
- Create: `ui/frontend/public/tiles/README.md` (instructions for re-acquiring the file)

- [ ] **Step 1: Add the tile file to .gitignore**

Append to the project root `.gitignore`:

```
# Protomaps tile file — too large for git; rebuild via public/tiles/README.md
ui/frontend/public/tiles/*.pmtiles
```

- [ ] **Step 2: Document acquisition**

Create `ui/frontend/public/tiles/README.md`:

```markdown
# Protomaps Tile File

The memorial map uses a self-hosted Protomaps `.pmtiles` file covering Israel.
The file is excluded from git because of size (~80 MB).

## Build or download

**Option A — download from the Protomaps public CDN, clipped to Israel:**

```bash
# Requires pmtiles CLI: https://docs.protomaps.com/pmtiles/cli
pmtiles extract https://build.protomaps.com/20260501.pmtiles \
  ui/frontend/public/tiles/israel.pmtiles \
  --bbox=34.2,29.5,35.9,33.5
```

**Option B — download the full planet file (~120 GB) and extract locally:**
See https://docs.protomaps.com/pmtiles for the canonical workflow.

## Verification

After download:

```bash
pmtiles show ui/frontend/public/tiles/israel.pmtiles
```

The output should report a non-zero tile count and a bounding box covering Israel.
```

- [ ] **Step 3: Acquire the file**

Follow Option A above. The file lands at `ui/frontend/public/tiles/israel.pmtiles`.

If `pmtiles` CLI is unavailable on the target machine, document the equivalent step the operator must run before the frontend dev server can show the map. The plan does not require this binary to be checked in.

- [ ] **Step 4: Commit (the README and .gitignore only)**

```bash
git add .gitignore ui/frontend/public/tiles/README.md
git commit -m "chore(ui): document protomaps tile acquisition"
```

---

## Phase 4 — Frontend types and API client

### Task 8: Rewrite `lib/api.ts` with new types and fetchers

**Files:**
- Modify (full rewrite): `ui/frontend/lib/api.ts`

- [ ] **Step 1: Replace the file**

Replace `ui/frontend/lib/api.ts` with:

```ts
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export interface DeathSummary {
  case_index: number;
  run_id: string;
  victim_name: string | null;
  victim_name_he: string | null;
  victim_name_ar: string | null;
  victim_age: number | null;
  incident_date: string | null;
  confidence_score: number | null;
}

export interface Locality {
  city: string;
  city_he: string | null;
  city_ar: string | null;
  lat: number;
  lng: number;
  death_count: number;
  most_recent_incident_date: string | null;
  deaths: DeathSummary[];
}

export interface MemorialResponse {
  run_id: string | null;
  year_range: { from: number | null; to: number | null };
  total_deaths: number;
  unresolved_count: number;
  localities: Locality[];
}

export interface Source {
  url: string;
  domain: string;
  published_at: string | null;
  title: string | null;
  role: string | null;
  tier: number | null;
}

export interface MediaItem {
  primary_url: string;
  type: string | null;
  is_evidence: boolean;
  caption: string | null;
}

export interface CaseDetail {
  case_index: number;
  run_id: string;
  victim_name: string | null;
  victim_name_he: string | null;
  victim_name_ar: string | null;
  victim_name_en: string | null;
  victim_age: number | null;
  victim_gender: string | null;
  incident_date: string | null;
  death_date: string | null;
  city: string | null;
  neighborhood: string | null;
  district: string | null;
  weapon_type: string | null;
  suspect_status: string | null;
  legal_status: string | null;
  case_narrative: string | null;
  sources: Source[];
  media_evidence: MediaItem[];
  conflict_map: Record<string, unknown> | null;
}

export async function fetchMemorial(): Promise<MemorialResponse> {
  const res = await fetch(`${API_BASE}/api/memorial`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchCase(runId: string, caseIndex: number): Promise<CaseDetail> {
  const res = await fetch(`${API_BASE}/api/cases/${runId}/${caseIndex}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
```

- [ ] **Step 2: Run a typecheck**

```bash
cd ui/frontend && npx tsc --noEmit
```

Expected: no errors. (At this point `bidi-name.tsx` is the only legacy component left, and it doesn't import from `lib/api.ts`.)

- [ ] **Step 3: Commit**

```bash
git add ui/frontend/lib/api.ts
git commit -m "feat(ui): rewrite api client around memorial types"
```

---

## Phase 5 — Map style and shell

### Task 9: Build the cream/charcoal MapLibre style

**Files:**
- Create: `ui/frontend/lib/map-style.ts`

- [ ] **Step 1: Create the style module**

Create `ui/frontend/lib/map-style.ts`:

```ts
import type { StyleSpecification } from "maplibre-gl";

const LAND = "#f5f1ea";
const WATER = "#ece6db";
const COASTLINE = "#2c2a26";

/**
 * MapLibre style for the memorial map.
 *
 * Uses a self-hosted Protomaps .pmtiles file via the `pmtiles://` protocol
 * (registered by the runtime in `memorial-map.tsx` before the map mounts).
 *
 * Goals:
 *  - Cream land, slightly cooler cream water, charcoal coastline.
 *  - No road network, no place labels until deep zoom.
 *  - Nothing competes with the locality dots.
 */
export function buildMemorialStyle(tilesUrl: string): StyleSpecification {
  return {
    version: 8,
    glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
    sources: {
      protomaps: {
        type: "vector",
        url: `pmtiles://${tilesUrl}`,
        attribution: '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org">OSM</a>',
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": LAND } },
      {
        id: "water",
        type: "fill",
        source: "protomaps",
        "source-layer": "water",
        paint: { "fill-color": WATER },
      },
      {
        id: "coastline",
        type: "line",
        source: "protomaps",
        "source-layer": "natural",
        filter: ["==", ["get", "kind"], "coastline"],
        paint: { "line-color": COASTLINE, "line-width": 1 },
      },
      {
        id: "country-borders",
        type: "line",
        source: "protomaps",
        "source-layer": "boundaries",
        filter: ["==", ["get", "kind"], "country"],
        paint: { "line-color": COASTLINE, "line-width": 0.5, "line-dasharray": [3, 2] },
      },
      // Only show city labels at deep zoom (>=10)
      {
        id: "place-labels",
        type: "symbol",
        source: "protomaps",
        "source-layer": "places",
        minzoom: 10,
        layout: {
          "text-field": ["get", "name"],
          "text-size": 10,
          "text-font": ["Noto Sans Regular"],
        },
        paint: {
          "text-color": COASTLINE,
          "text-halo-color": LAND,
          "text-halo-width": 1,
        },
      },
    ],
  };
}
```

- [ ] **Step 2: Verify it typechecks**

```bash
cd ui/frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add ui/frontend/lib/map-style.ts
git commit -m "feat(ui): add cream/charcoal maplibre style for memorial map"
```

---

### Task 10: Create the `MemorialMap` shell component

**Files:**
- Create: `ui/frontend/components/memorial-map.tsx`
- Modify: `ui/frontend/app/page.tsx`

- [ ] **Step 1: Create `memorial-map.tsx`**

Create `ui/frontend/components/memorial-map.tsx`:

```tsx
"use client";

import { useEffect, useRef } from "react";
import maplibregl, { Map } from "maplibre-gl";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildMemorialStyle } from "@/lib/map-style";
import type { MemorialResponse } from "@/lib/api";

interface MemorialMapProps {
  memorial: MemorialResponse;
}

// Israel + West Bank + Gaza + Golan bounding box
const INITIAL_BOUNDS: [[number, number], [number, number]] = [
  [34.2, 29.5], // SW
  [35.9, 33.5], // NE
];

const TILES_URL = "/tiles/israel.pmtiles";

export function MemorialMap({ memorial }: MemorialMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildMemorialStyle(window.location.origin + TILES_URL),
      bounds: INITIAL_BOUNDS,
      fitBoundsOptions: { padding: 40 },
      attributionControl: { compact: true },
      maxPitch: 0,
      dragRotate: false,
    });

    mapRef.current = map;

    return () => {
      maplibregl.removeProtocol("pmtiles");
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // memorial prop is unused here; consumed by the dot layer in a later task
  void memorial;

  return <div ref={containerRef} className="w-full h-screen" />;
}
```

- [ ] **Step 2: Wire `page.tsx` to fetch and render the map**

Replace `ui/frontend/app/page.tsx`:

```tsx
import { fetchMemorial } from "@/lib/api";
import { MemorialMap } from "@/components/memorial-map";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  let memorial;
  try {
    memorial = await fetchMemorial();
  } catch {
    memorial = {
      run_id: null,
      year_range: { from: null, to: null },
      total_deaths: 0,
      unresolved_count: 0,
      localities: [],
    };
  }

  return <MemorialMap memorial={memorial} />;
}
```

- [ ] **Step 3: Run the dev server and verify visually**

```bash
cd ui/frontend && npm run dev
```

Open `http://localhost:3000`. Expected: the cream map renders fitted to Israel; the API call to `/api/memorial` succeeds (with the API running on port 8001); no console errors.

If `israel.pmtiles` is missing, the background still renders cream (no coastline detail). That's an acceptable degraded state; the dots will be the next layer.

- [ ] **Step 4: Commit**

```bash
git add ui/frontend/components/memorial-map.tsx ui/frontend/app/page.tsx
git commit -m "feat(ui): render base memorial map with maplibre + protomaps"
```

---

## Phase 6 — Markers and pulse

### Task 11: Render static locality dots

**Files:**
- Modify: `ui/frontend/components/memorial-map.tsx`

- [ ] **Step 1: Add a GeoJSON layer for localities**

In `memorial-map.tsx`, extend the `useEffect` to add a source and a circle layer after the map's `load` event. Replace the `useEffect` body with:

```tsx
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildMemorialStyle(window.location.origin + TILES_URL),
      bounds: INITIAL_BOUNDS,
      fitBoundsOptions: { padding: 40 },
      attributionControl: { compact: true },
      maxPitch: 0,
      dragRotate: false,
    });

    mapRef.current = map;

    map.on("load", () => {
      const features = memorial.localities.map((loc) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [loc.lng, loc.lat] },
        properties: {
          city: loc.city,
          death_count: loc.death_count,
          pulse_weight: pulseWeight(loc.most_recent_incident_date),
        },
      }));

      map.addSource("localities", {
        type: "geojson",
        data: { type: "FeatureCollection", features },
      });

      // Static inner dot — radius scales with sqrt(death_count)
      map.addLayer({
        id: "locality-dot",
        type: "circle",
        source: "localities",
        paint: {
          "circle-color": "#8b2a1f",
          "circle-radius": [
            "min",
            14,
            ["+", 3, ["*", 2.5, ["sqrt", ["get", "death_count"]]]],
          ],
          "circle-stroke-width": 0.5,
          "circle-stroke-color": "#5a1b13",
        },
      });
    });

    return () => {
      maplibregl.removeProtocol("pmtiles");
      map.remove();
      mapRef.current = null;
    };
  }, [memorial]);
```

Add the `pulseWeight` helper above the component:

```tsx
function pulseWeight(mostRecentIncidentDate: string | null): number {
  if (!mostRecentIncidentDate) return 0;
  const incident = new Date(mostRecentIncidentDate).getTime();
  if (isNaN(incident)) return 0;
  const days = (Date.now() - incident) / (1000 * 60 * 60 * 24);
  return Math.max(0, 1 - days / 30);
}
```

- [ ] **Step 2: Verify visually**

Run the dev server, ensure the API has at least one died case in a known locality:

```bash
cd ui/frontend && npm run dev
```

Expected: a brick-red dot at the locality's lat/lng. Larger localities (more deaths) are larger dots.

- [ ] **Step 3: Commit**

```bash
git add ui/frontend/components/memorial-map.tsx
git commit -m "feat(ui): render static locality dots on the memorial map"
```

---

### Task 12: Add the pulse ring animation

**Files:**
- Modify: `ui/frontend/components/memorial-map.tsx`

- [ ] **Step 1: Add a pulse-ring layer + animation loop**

Inside the `map.on("load", () => { ... })` block in `memorial-map.tsx`, append (after `addLayer` for `"locality-dot"`):

```tsx
      // Pulse ring — outer circle whose opacity oscillates
      map.addLayer({
        id: "locality-pulse",
        type: "circle",
        source: "localities",
        paint: {
          "circle-color": "transparent",
          "circle-stroke-color": "#8b2a1f",
          "circle-stroke-width": 2,
          "circle-stroke-opacity": 0,
          "circle-radius": [
            "min",
            28,
            ["+", 8, ["*", 4, ["sqrt", ["get", "death_count"]]]],
          ],
        },
      }, "locality-dot");  // insert beneath the solid dot

      let raf = 0;
      const tick = () => {
        const t = performance.now() / 1000;
        const sine = (Math.sin(t * 1.8) + 1) / 2; // 0..1 at ~0.3 Hz
        map.setPaintProperty("locality-pulse", "circle-stroke-opacity", [
          "*",
          sine * 0.45,
          ["get", "pulse_weight"],
        ]);
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);

      map.once("remove", () => cancelAnimationFrame(raf));
```

- [ ] **Step 2: Verify visually**

Refresh `localhost:3000`. Expected: localities with a death in the last 30 days have a faint, slowly pulsing ring. Older-only localities show a static dot with no ring.

- [ ] **Step 3: Commit**

```bash
git add ui/frontend/components/memorial-map.tsx
git commit -m "feat(ui): add recency-weighted pulse ring to locality dots"
```

---

## Phase 7 — Bloom card

### Task 13: Bloom card — locality state

**Files:**
- Create: `ui/frontend/components/bloom-card.tsx`
- Modify: `ui/frontend/components/memorial-map.tsx`

- [ ] **Step 1: Create the bloom card component**

Create `ui/frontend/components/bloom-card.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import type { CaseDetail, DeathSummary, Locality } from "@/lib/api";
import { fetchCase } from "@/lib/api";
import { BidiName } from "./bidi-name";

interface BloomCardProps {
  locality: Locality;
  initialCaseIndex: number | null;
  screenPos: { x: number; y: number };
  onClose: () => void;
  onSelectCase: (caseIndex: number | null) => void;
}

export function BloomCard({
  locality,
  initialCaseIndex,
  screenPos,
  onClose,
  onSelectCase,
}: BloomCardProps) {
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialCaseIndex === null) {
      setCaseDetail(null);
      return;
    }
    let alive = true;
    setLoading(true);
    setError(null);
    fetchCase(locality.deaths[0].run_id, initialCaseIndex)
      .then((d) => alive && setCaseDetail(d))
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [initialCaseIndex, locality]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Edge-aware placement: flip if too close to right edge
  const FLIP_THRESHOLD = 360;
  const flipped = typeof window !== "undefined" && screenPos.x > window.innerWidth - FLIP_THRESHOLD;
  const style: React.CSSProperties = {
    position: "absolute",
    top: screenPos.y + 16,
    left: flipped ? undefined : screenPos.x + 16,
    right: flipped ? window.innerWidth - screenPos.x + 16 : undefined,
    zIndex: 30,
  };

  return (
    <div
      style={style}
      className="w-80 max-h-[80vh] overflow-y-auto rounded-lg border border-neutral-300 bg-white shadow-xl"
      onClick={(e) => e.stopPropagation()}
    >
      <header className="px-4 py-3 border-b border-neutral-200 flex items-center justify-between">
        <div className="space-y-0.5">
          <h2 className="text-sm font-semibold">
            <BidiName he={locality.city_he} ar={locality.city_ar} en={locality.city} />
          </h2>
          <p className="text-xs text-neutral-500">
            {locality.death_count} {locality.death_count === 1 ? "name" : "names"}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close" className="text-neutral-400 hover:text-neutral-700">
          ×
        </button>
      </header>

      {initialCaseIndex === null ? (
        <LocalityList deaths={locality.deaths} onSelect={onSelectCase} />
      ) : loading ? (
        <p className="p-4 text-xs text-neutral-500">Loading…</p>
      ) : error ? (
        <p className="p-4 text-xs text-red-700">Unable to load case detail.</p>
      ) : caseDetail ? (
        <CaseDetailBody c={caseDetail} onBack={() => onSelectCase(null)} />
      ) : null}
    </div>
  );
}

function LocalityList({
  deaths,
  onSelect,
}: {
  deaths: DeathSummary[];
  onSelect: (caseIndex: number) => void;
}) {
  return (
    <ul className="divide-y divide-neutral-100">
      {deaths.map((d) => (
        <li key={d.case_index}>
          <button
            onClick={() => onSelect(d.case_index)}
            className="w-full text-left px-4 py-2.5 hover:bg-neutral-50 flex items-baseline justify-between gap-2"
          >
            <span className="text-sm">
              <BidiName he={d.victim_name_he} ar={d.victim_name_ar} en={d.victim_name} />
              {d.victim_age !== null && (
                <span className="text-neutral-500 text-xs ml-1">· {d.victim_age}</span>
              )}
            </span>
            {d.incident_date && (
              <span className="text-xs text-neutral-400 tabular-nums">{d.incident_date}</span>
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}

function CaseDetailBody({ c, onBack }: { c: CaseDetail; onBack: () => void }) {
  return (
    <div className="p-4 space-y-3 text-sm">
      <button onClick={onBack} className="text-xs text-neutral-500 hover:text-neutral-900">
        ← back to locality
      </button>

      <h3 className="text-base font-semibold leading-tight">
        <BidiName he={c.victim_name_he} ar={c.victim_name_ar} en={c.victim_name} />
      </h3>

      <dl className="space-y-1 text-xs">
        {c.victim_age !== null && <DetailRow label="Age" value={String(c.victim_age)} />}
        {c.incident_date && <DetailRow label="Incident" value={c.incident_date} />}
        {c.death_date && <DetailRow label="Died" value={c.death_date} />}
        {c.weapon_type && <DetailRow label="Weapon" value={c.weapon_type} />}
        {c.suspect_status && <DetailRow label="Suspect" value={c.suspect_status} />}
        {c.legal_status && <DetailRow label="Legal" value={c.legal_status} />}
      </dl>

      {c.case_narrative && (
        <p className="text-xs text-neutral-600 border-t border-neutral-100 pt-2 leading-relaxed">
          {c.case_narrative}
        </p>
      )}

      {c.sources.length > 0 && (
        <div className="space-y-1 border-t border-neutral-100 pt-2">
          <p className="text-xs font-medium">Sources</p>
          <ul className="space-y-1">
            {c.sources.map((s, i) => (
              <li key={i} className="text-xs">
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-700 hover:underline break-all"
                >
                  {s.domain}
                </a>
                {s.published_at && <span className="text-neutral-400"> · {s.published_at}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-neutral-500 w-20 flex-shrink-0">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
```

- [ ] **Step 2: Wire click-to-open in `memorial-map.tsx`**

In `memorial-map.tsx`, add the bloom-card state and a click handler. Replace the entire file with:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl, { Map, MapMouseEvent } from "maplibre-gl";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildMemorialStyle } from "@/lib/map-style";
import type { Locality, MemorialResponse } from "@/lib/api";
import { BloomCard } from "./bloom-card";

interface MemorialMapProps {
  memorial: MemorialResponse;
}

const INITIAL_BOUNDS: [[number, number], [number, number]] = [
  [34.2, 29.5],
  [35.9, 33.5],
];
const TILES_URL = "/tiles/israel.pmtiles";

function pulseWeight(mostRecentIncidentDate: string | null): number {
  if (!mostRecentIncidentDate) return 0;
  const incident = new Date(mostRecentIncidentDate).getTime();
  if (isNaN(incident)) return 0;
  const days = (Date.now() - incident) / (1000 * 60 * 60 * 24);
  return Math.max(0, 1 - days / 30);
}

export function MemorialMap({ memorial }: MemorialMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const [selectedLocality, setSelectedLocality] = useState<Locality | null>(null);
  const [selectedCaseIndex, setSelectedCaseIndex] = useState<number | null>(null);
  const [screenPos, setScreenPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildMemorialStyle(window.location.origin + TILES_URL),
      bounds: INITIAL_BOUNDS,
      fitBoundsOptions: { padding: 40 },
      attributionControl: { compact: true },
      maxPitch: 0,
      dragRotate: false,
    });
    mapRef.current = map;

    map.on("load", () => {
      const features = memorial.localities.map((loc) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [loc.lng, loc.lat] },
        properties: {
          city: loc.city,
          death_count: loc.death_count,
          pulse_weight: pulseWeight(loc.most_recent_incident_date),
        },
      }));

      map.addSource("localities", {
        type: "geojson",
        data: { type: "FeatureCollection", features },
      });

      map.addLayer({
        id: "locality-pulse",
        type: "circle",
        source: "localities",
        paint: {
          "circle-color": "transparent",
          "circle-stroke-color": "#8b2a1f",
          "circle-stroke-width": 2,
          "circle-stroke-opacity": 0,
          "circle-radius": [
            "min",
            28,
            ["+", 8, ["*", 4, ["sqrt", ["get", "death_count"]]]],
          ],
        },
      });

      map.addLayer({
        id: "locality-dot",
        type: "circle",
        source: "localities",
        paint: {
          "circle-color": "#8b2a1f",
          "circle-radius": [
            "min",
            14,
            ["+", 3, ["*", 2.5, ["sqrt", ["get", "death_count"]]]],
          ],
          "circle-stroke-width": 0.5,
          "circle-stroke-color": "#5a1b13",
        },
      });

      let raf = 0;
      const tick = () => {
        const t = performance.now() / 1000;
        const sine = (Math.sin(t * 1.8) + 1) / 2;
        map.setPaintProperty("locality-pulse", "circle-stroke-opacity", [
          "*",
          sine * 0.45,
          ["get", "pulse_weight"],
        ]);
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      map.once("remove", () => cancelAnimationFrame(raf));

      map.on("mouseenter", "locality-dot", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "locality-dot", () => {
        map.getCanvas().style.cursor = "";
      });

      map.on("click", "locality-dot", (e: MapMouseEvent) => {
        const f = e.features?.[0];
        if (!f) return;
        const cityName = f.properties?.city as string | undefined;
        if (!cityName) return;
        const locality = memorial.localities.find((l) => l.city === cityName);
        if (!locality) return;
        const pt = e.point;
        setScreenPos({ x: pt.x, y: pt.y });
        setSelectedLocality(locality);
        setSelectedCaseIndex(null);
      });
    });

    return () => {
      maplibregl.removeProtocol("pmtiles");
      map.remove();
      mapRef.current = null;
    };
  }, [memorial]);

  return (
    <div className="relative w-full h-screen" onClick={() => setSelectedLocality(null)}>
      <div ref={containerRef} className="absolute inset-0" />
      {selectedLocality && (
        <BloomCard
          locality={selectedLocality}
          initialCaseIndex={selectedCaseIndex}
          screenPos={screenPos}
          onClose={() => setSelectedLocality(null)}
          onSelectCase={setSelectedCaseIndex}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: Verify visually**

Run `npm run dev`. Click a dot. Expected: a card appears next to it with the locality name and a list of victim names. Click a name. Expected: card swaps to the case detail. Click the back link. Expected: returns to locality view. Press ESC. Expected: card closes.

- [ ] **Step 4: Commit**

```bash
git add ui/frontend/components/bloom-card.tsx ui/frontend/components/memorial-map.tsx
git commit -m "feat(ui): add bloom card with locality and case states"
```

---

## Phase 8 — Chrome (title, scrubber, count)

### Task 14: Add the project title and death count overlays

**Files:**
- Create: `ui/frontend/components/death-count.tsx`
- Modify: `ui/frontend/components/memorial-map.tsx`

- [ ] **Step 1: Create `death-count.tsx`**

Create `ui/frontend/components/death-count.tsx`:

```tsx
"use client";

interface DeathCountProps {
  count: number;
}

export function DeathCount({ count }: DeathCountProps) {
  return (
    <div className="absolute bottom-4 right-4 z-20 text-xs text-neutral-700 bg-white/80 backdrop-blur px-2 py-1 rounded">
      <span className="tabular-nums font-semibold">{count}</span>{" "}
      <span className="text-neutral-500">{count === 1 ? "name" : "names"}</span>
    </div>
  );
}
```

- [ ] **Step 2: Add title + count in `memorial-map.tsx`**

In `MemorialMap`, replace the returned JSX with:

```tsx
  const visibleCount = memorial.localities.reduce((sum, l) => sum + l.death_count, 0);

  return (
    <div className="relative w-full h-screen" onClick={() => setSelectedLocality(null)}>
      <div ref={containerRef} className="absolute inset-0" />

      <div className="absolute top-3 left-4 z-20 text-xs font-medium text-neutral-700 tracking-wide">
        Crime Pipeline — Memorial
      </div>

      <DeathCount count={visibleCount} />

      {selectedLocality && (
        <BloomCard
          locality={selectedLocality}
          initialCaseIndex={selectedCaseIndex}
          screenPos={screenPos}
          onClose={() => setSelectedLocality(null)}
          onSelectCase={setSelectedCaseIndex}
        />
      )}
    </div>
  );
```

Add at the top of the file: `import { DeathCount } from "./death-count";`

- [ ] **Step 3: Commit**

```bash
git add ui/frontend/components/death-count.tsx ui/frontend/components/memorial-map.tsx
git commit -m "feat(ui): add project title and death count chrome"
```

---

### Task 15: Add the year-range scrubber with client-side filtering

**Files:**
- Create: `ui/frontend/components/year-scrubber.tsx`
- Modify: `ui/frontend/components/memorial-map.tsx`

- [ ] **Step 1: Create `year-scrubber.tsx`**

Create `ui/frontend/components/year-scrubber.tsx`:

```tsx
"use client";

interface YearScrubberProps {
  min: number;
  max: number;
  from: number;
  to: number;
  onChange: (from: number, to: number) => void;
}

export function YearScrubber({ min, max, from, to, onChange }: YearScrubberProps) {
  return (
    <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 w-80 max-w-[80vw] bg-white/80 backdrop-blur rounded px-3 py-2 space-y-1">
      <div className="flex justify-between text-[10px] text-neutral-500 tabular-nums">
        <span>{from}</span>
        <span>{to}</span>
      </div>
      <div className="flex gap-2 items-center">
        <input
          type="range"
          min={min}
          max={max}
          step={1}
          value={from}
          onChange={(e) => onChange(Math.min(Number(e.target.value), to), to)}
          className="flex-1 accent-neutral-700"
          aria-label="Year from"
        />
        <input
          type="range"
          min={min}
          max={max}
          step={1}
          value={to}
          onChange={(e) => onChange(from, Math.max(Number(e.target.value), from))}
          className="flex-1 accent-neutral-700"
          aria-label="Year to"
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add filtering logic in `memorial-map.tsx`**

At the top of `MemorialMap`, just after the existing `useState` hooks, add year-range state and a derived filtered locality list:

```tsx
  const yearMin = memorial.year_range.from ?? new Date().getFullYear();
  const yearMax = memorial.year_range.to ?? new Date().getFullYear();
  const [yearFrom, setYearFrom] = useState(yearMin);
  const [yearTo, setYearTo] = useState(yearMax);

  const filteredLocalities = useMemo(() => {
    return memorial.localities
      .map((loc) => {
        const deaths = loc.deaths.filter((d) => {
          if (!d.incident_date) return true;
          const y = Number(d.incident_date.slice(0, 4));
          return y >= yearFrom && y <= yearTo;
        });
        return { ...loc, deaths, death_count: deaths.length };
      })
      .filter((l) => l.death_count > 0);
  }, [memorial, yearFrom, yearTo]);
```

Add `useMemo` to the React import: `import { useEffect, useMemo, useRef, useState } from "react";`

Replace the line that constructs `features` inside `map.on("load", ...)` so it uses `filteredLocalities` instead of `memorial.localities`. Then, **outside** that block, add an effect that re-pushes data when the filtered list changes:

```tsx
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const src = map.getSource("localities");
    if (!src || src.type !== "geojson") return;
    const features = filteredLocalities.map((loc) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [loc.lng, loc.lat] },
      properties: {
        city: loc.city,
        death_count: loc.death_count,
        pulse_weight: pulseWeight(loc.most_recent_incident_date),
      },
    }));
    (src as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features });
  }, [filteredLocalities]);
```

Update the `visibleCount` line to:

```tsx
  const visibleCount = filteredLocalities.reduce((sum, l) => sum + l.death_count, 0);
```

The click handler inside `map.on("load", ...)` is registered once and closes over the initial `memorial.localities`. To make it see the currently filtered list, add a ref that mirrors `filteredLocalities` and rewrite the click lookup to use it. Add this just below the `useMemo`:

```tsx
  const filteredLocalitiesRef = useRef(filteredLocalities);
  useEffect(() => {
    filteredLocalitiesRef.current = filteredLocalities;
  }, [filteredLocalities]);
```

Then change the click handler inside `map.on("load", ...)` from:

```tsx
        const locality = memorial.localities.find((l) => l.city === cityName);
```

to:

```tsx
        const locality = filteredLocalitiesRef.current.find((l) => l.city === cityName);
```

This keeps the bloom card consistent with the scrubber — clicking a dot always shows only the deaths that match the current year range.

Finally, render the scrubber in the JSX (just before the bloom card):

```tsx
      <YearScrubber
        min={yearMin}
        max={yearMax}
        from={yearFrom}
        to={yearTo}
        onChange={(f, t) => {
          setYearFrom(f);
          setYearTo(t);
        }}
      />
```

Add the import: `import { YearScrubber } from "./year-scrubber";`

- [ ] **Step 3: Verify visually**

Run `npm run dev`. Drag the scrubber thumbs. Expected: dots disappear/reappear as the range tightens/widens. The count updates accordingly.

- [ ] **Step 4: Commit**

```bash
git add ui/frontend/components/year-scrubber.tsx ui/frontend/components/memorial-map.tsx
git commit -m "feat(ui): add year-range scrubber with client-side filtering"
```

---

### Task 16: URL sync for selected locality + case

**Files:**
- Modify: `ui/frontend/components/memorial-map.tsx`

- [ ] **Step 1: Sync URL → state and state → URL**

In `MemorialMap`, add a `slugify` helper and a sync effect.

At the top of the file (outside the component):

```tsx
function slugify(city: string): string {
  return city.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
```

Inside the component, add an effect that reads URL params on mount and another that updates them when selection changes:

```tsx
  // Read URL on first paint
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const slug = params.get("locality");
    if (!slug) return;
    const loc = memorial.localities.find((l) => slugify(l.city) === slug);
    if (!loc) return;
    setSelectedLocality(loc);
    setScreenPos({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
    const caseStr = params.get("case");
    if (caseStr) {
      const idx = Number(caseStr);
      if (!isNaN(idx)) setSelectedCaseIndex(idx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Write URL on selection change
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (selectedLocality) {
      params.set("locality", slugify(selectedLocality.city));
      if (selectedCaseIndex !== null) {
        params.set("case", String(selectedCaseIndex));
      } else {
        params.delete("case");
      }
    } else {
      params.delete("locality");
      params.delete("case");
    }
    const qs = params.toString();
    const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
    window.history.replaceState(null, "", url);
  }, [selectedLocality, selectedCaseIndex]);
```

- [ ] **Step 2: Verify visually**

Run `npm run dev`. Click a dot — URL becomes `?locality=tira`. Click a name — URL becomes `?locality=tira&case=12`. Reload the page — the card reopens to the same state.

- [ ] **Step 3: Commit**

```bash
git add ui/frontend/components/memorial-map.tsx
git commit -m "feat(ui): sync bloom card selection to URL params"
```

---

## Phase 9 — Error handling

### Task 17: Visible failure mode for missing memorial data

**Files:**
- Modify: `ui/frontend/components/memorial-map.tsx`

- [ ] **Step 1: Add a banner when total_deaths is 0 from a real (run_id != null) call**

In the JSX returned by `MemorialMap`, add (just below the title overlay):

```tsx
      {memorial.run_id === null && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 text-xs text-neutral-500 bg-white/80 backdrop-blur px-2 py-1 rounded">
          Unable to load memorial data.
        </div>
      )}
```

(`page.tsx` already returns `run_id: null` when the fetch fails; the API also returns `run_id: null` if no runs exist. Both are acceptable triggers for this banner.)

- [ ] **Step 2: Commit**

```bash
git add ui/frontend/components/memorial-map.tsx
git commit -m "feat(ui): surface memorial-data load failure as a small banner"
```

---

## Phase 10 — Frontend smoke tests

### Task 18: Add Playwright smoke tests for the memorial map

**Files:**
- Modify: `ui/frontend/package.json` (add Playwright dev deps + script)
- Create: `ui/frontend/playwright.config.ts`
- Create: `ui/frontend/tests/fixtures/memorial.json`
- Create: `ui/frontend/tests/memorial-map.spec.ts`
- Create: `ui/frontend/tests/bloom-card.spec.ts`

This task adds two smoke tests with stubbed network: the map renders dots from a fixture payload, and the bloom card opens/closes correctly. The tests run against `npm run dev` with API requests intercepted by Playwright's `page.route()`, so they do not require the FastAPI backend to be running.

- [ ] **Step 1: Install Playwright**

```bash
cd ui/frontend
npm install --save-dev @playwright/test@^1.49.0
npx playwright install chromium
```

- [ ] **Step 2: Add the Playwright config**

Create `ui/frontend/playwright.config.ts`:

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
```

- [ ] **Step 3: Add the fixture payload**

Create `ui/frontend/tests/fixtures/memorial.json`:

```json
{
  "run_id": "test-run",
  "year_range": { "from": 2024, "to": 2026 },
  "total_deaths": 3,
  "unresolved_count": 0,
  "localities": [
    {
      "city": "Tira",
      "city_he": "טירה",
      "city_ar": "الطيرة",
      "lat": 32.2333,
      "lng": 34.9500,
      "death_count": 2,
      "most_recent_incident_date": "2026-04-19",
      "deaths": [
        {
          "case_index": 0,
          "run_id": "test-run",
          "victim_name": "Alice",
          "victim_name_he": "אליס",
          "victim_name_ar": "أليس",
          "victim_age": 24,
          "incident_date": "2026-04-19",
          "confidence_score": 0.9
        },
        {
          "case_index": 1,
          "run_id": "test-run",
          "victim_name": "Bob",
          "victim_name_he": null,
          "victim_name_ar": null,
          "victim_age": 33,
          "incident_date": "2025-12-01",
          "confidence_score": 0.85
        }
      ]
    },
    {
      "city": "Arraba",
      "city_he": "עראבה",
      "city_ar": "عرابة",
      "lat": 32.8517,
      "lng": 35.3361,
      "death_count": 1,
      "most_recent_incident_date": "2024-08-10",
      "deaths": [
        {
          "case_index": 2,
          "run_id": "test-run",
          "victim_name": "Carol",
          "victim_name_he": null,
          "victim_name_ar": null,
          "victim_age": 41,
          "incident_date": "2024-08-10",
          "confidence_score": 0.92
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Write the map render test**

Create `ui/frontend/tests/memorial-map.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import memorialFixture from "./fixtures/memorial.json";

test("renders the memorial map with the death count", async ({ page }) => {
  await page.route("**/api/memorial", (route) =>
    route.fulfill({ json: memorialFixture })
  );

  await page.goto("/");

  // Map container is present
  await expect(page.locator(".maplibregl-canvas")).toBeVisible({ timeout: 10_000 });

  // Title and count chrome render
  await expect(page.getByText("Crime Pipeline — Memorial")).toBeVisible();
  await expect(page.getByText("3", { exact: false })).toBeVisible();
  await expect(page.getByText("names")).toBeVisible();
});
```

- [ ] **Step 5: Write the bloom-card test**

Create `ui/frontend/tests/bloom-card.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import memorialFixture from "./fixtures/memorial.json";

test("bloom card opens, lists victims, swaps to case detail, closes on ESC", async ({ page }) => {
  await page.route("**/api/memorial", (route) =>
    route.fulfill({ json: memorialFixture })
  );
  await page.route("**/api/cases/**", (route) =>
    route.fulfill({
      json: {
        case_index: 0,
        run_id: "test-run",
        victim_name: "Alice",
        victim_name_he: "אליס",
        victim_name_ar: "أليس",
        victim_name_en: "Alice",
        victim_age: 24,
        victim_gender: "female",
        incident_date: "2026-04-19",
        death_date: "2026-04-19",
        city: "Tira",
        neighborhood: null,
        district: null,
        weapon_type: "firearm",
        suspect_status: null,
        legal_status: null,
        case_narrative: "A test narrative.",
        sources: [],
        media_evidence: [],
        conflict_map: null,
      },
    })
  );

  await page.goto("/");
  await page.waitForSelector(".maplibregl-canvas");

  // Click the Tira dot via its projected position.
  // We can't click the canvas at exact map coords from Playwright easily, so we
  // simulate via the click handler exposed by the source-layer. Use the locality
  // dot's hit detection by clicking the center of the canvas first, then drilling
  // in by URL deep-link.
  await page.goto("/?locality=tira");

  // Locality state shows victim names
  await expect(page.getByText("Alice")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText("Bob")).toBeVisible();

  // Click "Alice" to drill into the case state
  await page.getByText("Alice").click();
  await expect(page.getByText("A test narrative.")).toBeVisible();

  // ESC closes the card
  await page.keyboard.press("Escape");
  await expect(page.getByText("A test narrative.")).toBeHidden();
});
```

- [ ] **Step 6: Add a test script to package.json**

In `ui/frontend/package.json`, add to `scripts`:

```json
    "test:e2e": "playwright test"
```

- [ ] **Step 7: Run the tests**

```bash
cd ui/frontend && npm run test:e2e
```

Expected: 2 passed. If the bloom-card test fails because the deep-link doesn't populate the card on first paint, increase the timeout or assert against the URL-sync effect's post-mount state.

- [ ] **Step 8: Commit**

```bash
git add ui/frontend/playwright.config.ts ui/frontend/tests/ ui/frontend/package.json ui/frontend/package-lock.json
git commit -m "test(ui): add playwright smoke tests for memorial map and bloom card"
```

---

## Phase 11 — Update CLAUDE.md

### Task 19: Document the new architecture in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Append a UI section**

Add to `CLAUDE.md` just before the final test-patterns section:

````markdown
---

## Frontend — Memorial Map

The frontend (`ui/frontend/`) is a single-page Next.js memorial. There is no list view,
no filter sidebar, no review queue. The map is the index.

Key files:
- `app/page.tsx` — the only route; server-fetches `/api/memorial` and renders the map.
- `components/memorial-map.tsx` — MapLibre canvas + GeoJSON locality layer + pulse loop.
- `components/bloom-card.tsx` — inline card with locality and case states.
- `components/year-scrubber.tsx`, `components/death-count.tsx` — peripheral chrome.
- `lib/map-style.ts` — cream/charcoal MapLibre style for the Protomaps source.
- `public/tiles/israel.pmtiles` — self-hosted tile file (gitignored; see `public/tiles/README.md`).

Backend dependency:
- `GET /api/memorial` aggregates the latest run's `died` cases by locality, attaching
  `lat`/`lng` from the gazetteer. Cases without a gazetteer match are counted in
  `unresolved_count` and excluded.

Start order:
```bash
uvicorn ui.api.main:app --reload --port 8001   # backend
cd ui/frontend && npm run dev                  # frontend (port 3000)
```
````

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document the memorial map frontend in CLAUDE.md"
```

---

## Self-review checklist (run before reporting done)

- [ ] `pytest tests/test_gazetteer_coords.py tests/test_memorial_endpoint.py -v` — all PASS.
- [ ] `ruff check crime_pipeline ui/api` — clean.
- [ ] `mypy crime_pipeline` — clean.
- [ ] `cd ui/frontend && npx tsc --noEmit` — clean.
- [ ] `cd ui/frontend && npm run build` — succeeds.
- [ ] `cd ui/frontend && npm run test:e2e` — 2 passed.
- [ ] `uvicorn ui.api.main:app --reload --port 8001` running.
- [ ] `cd ui/frontend && npm run dev` — open `http://localhost:3000`. Confirm:
  - Map renders, cream + charcoal, fitted to Israel.
  - At least one locality dot is visible if a run with `died` cases exists.
  - Recent localities pulse; old-only localities do not.
  - Click a dot → bloom card shows victim list.
  - Click a name → case detail loads.
  - ESC closes the card.
  - Year scrubber filters dots and updates the count.
  - URL syncs to `?locality=…&case=…`.

If any check fails, fix in place and re-commit. Do not mark the implementation done with a failing check.
