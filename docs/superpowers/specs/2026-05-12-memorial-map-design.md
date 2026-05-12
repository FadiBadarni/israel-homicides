# Memorial Map — Frontend Redesign

**Date:** 2026-05-12
**Status:** Spec — pending implementation plan
**Author:** Brainstormed with Claude

---

## 1. Intent

Replace the existing case-browser UI with a single-page memorial. The map of Israel is the entire interface. Each locality where a homicide victim has died appears as a small, restrained dot. Recent deaths cause the dot to pulse softly for 30 days. Clicking a dot reveals who died there.

The page is the index. There is no list view, no filter sidebar, no table, no review queue. Survivors and non-fatal cases are excluded from this surface (they still exist in the pipeline and the database; they are simply not part of the memorial).

---

## 2. Scope

### In scope

- A new single-page Next.js route (`/`) replacing all current frontend routes.
- A backend gazetteer extension that attaches latitude/longitude to every known locality.
- A new API endpoint that returns the memorial dataset in one payload.
- Removal of the existing `ui/frontend/app/cases/`, `ui/frontend/app/review/` routes and the components that supported them (where not reusable).

### Out of scope

- Authentication, user accounts, write actions.
- A second view for non-fatal cases.
- Real-time updates / websockets — the page reloads to refresh.
- Mobile-specific layout beyond a responsive map and a usable bloom card.
- Internationalization of UI chrome (the data is multilingual; the chrome is English-only).
- Print stylesheet, share/export features.
- Changes to the pipeline, dedup, extraction, or any module outside `ui/api/` and the gazetteer.

### Non-goals

- "Quiet" is a design constraint, not a starting point to be re-debated. Anything that adds visual noise to the default canvas (additional badges, multiple accent colors, animated transitions on hover beyond the pulse) is rejected by default.

---

## 3. Architecture

### 3.1 Backend

Two changes, both small.

**A. Gazetteer coordinates.** `crime_pipeline/utils/gazetteer.py::CityRecord` gains `lat: float` and `lng: float`. The underlying data file (the gazetteer's source-of-truth dictionary or JSON) is augmented with coordinates for every locality currently present in the truth files and pipeline outputs. Coordinates are stored as WGS84 decimal degrees, four decimal places.

**B. Memorial endpoint.** `ui/api/main.py` gains one new endpoint:

```
GET /api/memorial?year_from=YYYY&year_to=YYYY
```

Returns a single JSON payload aggregating every died-outcome case from the most recent run, grouped by locality:

```json
{
  "run_id": "...",
  "year_range": { "from": 2023, "to": 2026 },
  "total_deaths": 247,
  "localities": [
    {
      "city": "Tira",
      "city_he": "טירה",
      "city_ar": "الطيرة",
      "lat": 32.2333,
      "lng": 34.9500,
      "death_count": 5,
      "most_recent_incident_date": "2026-04-19",
      "deaths": [
        {
          "case_index": 12,
          "run_id": "...",
          "victim_name": "...",
          "victim_name_he": "...",
          "victim_name_ar": "...",
          "victim_age": 24,
          "incident_date": "2026-04-19",
          "confidence_score": 0.91
        }
      ]
    }
  ]
}
```

Cases without a resolvable locality (gazetteer miss) are excluded with a count exposed at the top level (`unresolved_count`). Cases with `victim_outcome != "died"` are excluded entirely.

The existing `GET /api/cases/{run_id}/{case_index}` endpoint is reused for the per-case detail expansion. No other changes to the API.

Endpoints to remove from `ui/api/main.py`: none. The current `/api/cases`, `/api/filters`, `/api/stats`, `/api/runs`, `/api/review-pairs` endpoints remain so the API stays useful for diagnostics and future tooling. Only the frontend stops consuming them.

### 3.2 Frontend

Single Next.js page. Server-renders the initial memorial payload from the API, hydrates the map client-side.

```
ui/frontend/app/page.tsx              # the only route; renders the map
ui/frontend/components/memorial-map.tsx
ui/frontend/components/locality-dot-layer.tsx    # MapLibre layer config + pulse
ui/frontend/components/bloom-card.tsx            # locality → cases → case detail
ui/frontend/components/year-scrubber.tsx
ui/frontend/components/death-count.tsx
ui/frontend/lib/api.ts                # trimmed to memorial + case-detail fetchers
ui/frontend/lib/map-style.ts          # cream/charcoal MapLibre style JSON
ui/frontend/public/tiles/israel.pmtiles  # self-hosted Protomaps tiles for the region
```

To delete:

```
ui/frontend/app/cases/                # whole tree
ui/frontend/app/review/
ui/frontend/components/cases-table.tsx
ui/frontend/components/case-filters.tsx
ui/frontend/components/confidence-badge.tsx
ui/frontend/components/outcome-badge.tsx
ui/frontend/components/case-detail.tsx
ui/frontend/components/media-gallery.tsx
ui/frontend/components/bidi-name.tsx   # may be reused inside bloom-card; decide during impl
```

`bidi-name.tsx` is the one ambiguous case: the bloom card needs bidi-isolated name rendering, and this component already does it cleanly. The implementation plan will either keep it in place or inline it into `bloom-card.tsx`, whichever yields the simpler footprint.

### 3.3 Map technology

- **Library:** `maplibre-gl` (4.x).
- **Tiles:** Self-hosted Protomaps single-file `.pmtiles` for Israel + West Bank + Gaza + Sinai border. Served as a static asset from `ui/frontend/public/tiles/israel.pmtiles`. Approximate size: 60–120 MB; one-time download per visitor (cached aggressively).
- **Style:** custom JSON. Cream `#f5f1ea` land fill, charcoal `#2c2a26` coastline (1 px), no roads visible at low zoom, no city labels until zoom ≥ 10. The Mediterranean and Dead Sea render as a slightly cooler cream `#ece6db` — present, not loud.
- **Initial view:** fitted bounds covering 29.5°N to 33.5°N, 34.2°E to 35.9°E. Pan and zoom enabled; tilt disabled.

### 3.4 Markers and pulse

Each locality is a `circle` layer feature in MapLibre. Two visual elements per locality:

1. A solid inner circle, radius = `3 + sqrt(death_count) * 2.5` capped at 14 px. Color: a single restrained tone, `#8b2a1f` (warm brick-red).
2. An outer ring whose opacity oscillates as a sine function of time. The amplitude is scaled by the locality's pulse weight, where `pulse_weight = max(case.pulse_contribution for case in locality.deaths)` and `case.pulse_contribution = max(0, 1 - days_since_incident / 30)`. A locality whose deaths are all older than 30 days has pulse weight 0 — the ring is invisible. A locality with a death yesterday pulses at full amplitude.

The pulse is computed client-side, redrawn on a `requestAnimationFrame` loop at ~30 fps. The pulse is the **only** animation on the page.

### 3.5 Bloom card

The bloom card is a single component with three internal states:

- **Closed:** not rendered.
- **Locality state:** opens when a dot is clicked. Shows locality name in three scripts (Hebrew, Arabic, English transliteration), a death count, and a vertical list of victim names. Each name row shows the name (bidi-isolated), age if known, and incident date.
- **Case state:** triggered by clicking a victim name inside the locality state. Replaces the list with a full case view: narrative (if present), incident date, death date, weapon, suspect status, legal status, sources (linked, with domain + tier), media (evidence-only), conflict map if non-empty. A back affordance returns to the locality state.

The card is positioned next to the clicked dot, with edge-aware placement (flips to the opposite side if it would overflow the viewport). The card never covers the dot it belongs to. Click-away or ESC closes it.

The URL syncs to `?locality=<city_slug>` for the locality state and `?locality=<city_slug>&case=<run_id>:<case_index>` for the case state. Loading the page with these params restores the corresponding state on hydration.

### 3.6 Year scrubber and count

A thin horizontal slider at the bottom-center. Two thumbs (`year_from`, `year_to`). The available range is computed from the earliest and latest `incident_date` in the dataset. Dragging either thumb refilters the map client-side (no re-fetch — the full payload is in memory).

The count at bottom-right shows the number of *names* currently visible at the selected year range. Format: `247 names`. Updates as the scrubber moves.

### 3.7 Data flow

```
Page load (server)
  └─ fetch /api/memorial → seed initial payload (RSC)
       └─ render <html> with map container + serialized payload

Client hydration
  └─ MapLibre initializes with custom style + .pmtiles
  └─ Locality features added as a GeoJSON source
  └─ Year scrubber & count read from the same payload
  └─ Pulse animation loop starts

User interaction
  ├─ Click locality dot
  │    └─ open bloom card (locality state)
  │    └─ push URL ?locality=…
  ├─ Click victim name
  │    └─ fetch /api/cases/{run_id}/{case_index}
  │    └─ swap bloom card to case state
  │    └─ push URL ?locality=…&case=…
  └─ Drag year scrubber
       └─ filter localities client-side
       └─ recompute count
       └─ (URL is NOT synced for scrubber — transient state)
```

---

## 4. Data model

### 4.1 Backend additions

`CityRecord` gains:

```python
class CityRecord:
    name_en: str
    name_he: str | None
    name_ar: str | None
    district: str | None
    region: str | None
    lat: float   # NEW
    lng: float   # NEW
```

The gazetteer data file is updated with coordinates. Every locality referenced by the existing truth files (`data/truth_*.jsonl`) must have coordinates; coverage of less common localities can grow as needed.

### 4.2 Frontend types

```ts
interface MemorialResponse {
  run_id: string;
  year_range: { from: number; to: number };
  total_deaths: number;
  unresolved_count: number;
  localities: Locality[];
}

interface Locality {
  city: string;
  city_he: string | null;
  city_ar: string | null;
  lat: number;
  lng: number;
  death_count: number;
  most_recent_incident_date: string | null;
  deaths: DeathSummary[];
}

interface DeathSummary {
  case_index: number;
  run_id: string;
  victim_name: string | null;
  victim_name_he: string | null;
  victim_name_ar: string | null;
  victim_age: number | null;
  incident_date: string | null;
  confidence_score: number | null;
}
```

Case detail reuses the existing `CaseDetail` shape from the current `lib/api.ts`.

---

## 5. Error handling

| Failure | Behavior |
|--------|---------|
| `/api/memorial` returns 5xx | Page renders the map with no dots and a small line at bottom-center: "Unable to load memorial data." |
| Gazetteer miss on a city (no coords) | Case is dropped server-side; counted toward `unresolved_count`. Not surfaced in the UI by default. |
| `.pmtiles` fails to load | Map background renders as flat cream; dots still appear at the correct lat/lng. Page remains functional. |
| `/api/cases/{run_id}/{case_index}` fails when expanding a victim | Bloom card shows the locality state with an inline error row for that name. Other names remain clickable. |
| Bad URL params (`?locality=unknown` or malformed case ID) | Silently ignored; page opens in default state. |

All errors are logged to the browser console with `structlog`-style structured fields (`event`, `error`, `context`).

---

## 6. Testing

### 6.1 Backend

Pytest, following the project's existing patterns (no network, `asyncio.run` wrapping in `tests/conftest.py`):

- `test_gazetteer_coords.py` — every locality used by `data/truth_*.jsonl` resolves to a `CityRecord` with non-null `lat` and `lng`.
- `test_memorial_endpoint.py` — given a fixture run file containing a mix of `died`, `survived`, `critical`, `unknown` cases, the endpoint returns only `died` cases, grouped correctly by locality, sorted by `death_count` desc.
- `test_memorial_endpoint_year_filter.py` — `year_from` and `year_to` are inclusive and filter on `incident_date`.
- `test_memorial_endpoint_unresolved.py` — a case with a city not in the gazetteer increments `unresolved_count` and does not appear in `localities`.

### 6.2 Frontend

Light-touch — this is a small surface. One smoke test for the map page render and one for the bloom card state machine. Use Playwright (already installed at the repo level for the Python scraper, but a fresh Node-side install is acceptable) **or** React Testing Library + jsdom if Playwright is too heavy for the first iteration. The implementation plan picks one.

- `memorial-map.spec.ts` — page renders, map canvas mounts, dots appear at expected coordinates given a fixture payload.
- `bloom-card.spec.ts` — clicking a dot opens locality state; clicking a name opens case state; ESC closes.

### 6.3 Manual verification checklist (no automated test for these — visual)

- Map opens fitted to Israel; no labels visible at default zoom.
- Pulse is calm (not flashy). A dot with a death older than 30 days does not pulse.
- Bloom card flips when near viewport edge.
- Year scrubber filters the visible dots and updates the count.
- Hebrew and Arabic names render with correct bidi isolation inside the card.

---

## 7. Performance

The dataset is small (estimated 200–600 cases, 80–120 localities). The full memorial payload is well under 200 KB gzipped. No clustering, no tile-based fetching, no virtualization needed.

The pulse animation runs at ~30 fps via `requestAnimationFrame` and only updates the opacity of the outer-ring layer — MapLibre handles GPU-side compositing. Estimated CPU cost: negligible.

The `.pmtiles` file is the heaviest asset (60–120 MB). Served with long `Cache-Control` headers; loaded once per visitor.

---

## 8. Migration

This is a hard cutover. There is no flag, no parallel deployment, no preserved old UI. On the deploy that ships this:

1. Backend gazetteer is updated with coordinates and the `/api/memorial` endpoint goes live.
2. Frontend is rebuilt with the new single page.
3. The old routes (`/cases`, `/review`) return Next.js 404s. No redirects (they had no external linking story worth preserving).

The pipeline DB and the existing JSON output files are untouched. The pipeline keeps producing the same output; the memorial endpoint just reads it differently.

---

## 9. Open implementation questions

These are intentionally deferred to the implementation plan, not to a second round of brainstorming:

- **Pulse animation frame cadence**: 30 fps is the target, but if profiling shows jitter on lower-end hardware we may drop to 15 fps. Decided during impl.
- **Locality color when `death_count > 1`**: the current spec says one color regardless. If during implementation it becomes visually unclear that a 5-death locality is different from a 1-death locality at standard zoom, we may add a subtle inner-radius treatment (a darker core). Not a redesign — a refinement of the size encoding.
- **`bidi-name.tsx` reuse vs. inline**: see §3.2.
- **Test framework on the frontend**: Playwright vs. RTL+jsdom — see §6.2.

---

## 10. Success criteria

The redesign is successful when all of the following are true:

1. Visiting `/` shows a quiet map of Israel with restrained brick-red dots at every locality where a homicide victim has died.
2. Recently-deceased locality dots pulse; older ones do not.
3. Clicking a dot reveals the names of victims in that locality, without leaving the page.
4. Clicking a name reveals the full case detail in the same bloom card.
5. The year scrubber filters the visible dots and the bottom-right count updates accordingly.
6. The page has no list, table, filter sidebar, or review queue.
7. The backend gazetteer resolves every locality currently referenced in `data/truth_*.jsonl` to coordinates.
8. The pipeline, dedup, extraction, and all backend modules outside `ui/api/` and `crime_pipeline/utils/gazetteer.py` are unchanged.

---

*End of spec.*
