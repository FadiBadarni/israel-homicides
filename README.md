# سجل ضحايا الجريمة في المجتمع العربي

A public register documenting homicide victims in Arab society in Israel — name by name.

The project has two parts: a **data pipeline** that discovers, extracts, deduplicates, and merges case information from Arabic and Hebrew news sources, and a **memorial frontend** that presents the data as a bilingual (Arabic / Hebrew) interactive register.

---

## Memorial Frontend

Next.js app deployed on Vercel. No backend required in production — reads pre-exported static JSON.

**Pages:**

| Route | Description |
|-------|-------------|
| `/` | Hero with animated stats, year timeline, searchable victim cards, contact strip |
| `/cases/[runId]/[caseIndex]` | Victim memorial — photo gallery, incident facts, source citations |
| `/contribute` | Call to action for data contributions |

**Features:**
- Bilingual Arabic / Hebrew with full RTL support and language toggle
- Victim search across all name scripts and transliterations
- Animated count-up stats (respects `prefers-reduced-motion`)
- Smooth page transitions
- Responsive: desktop, tablet, and 2-column phone layouts
- Vercel Analytics

### Run the frontend locally

```bash
cd ui/frontend
npm install
npm run dev          # http://localhost:3000
```

### Refresh data and deploy

```bash
python scripts/export_static_data.py     # regenerate public/data/ from SQLite
cd ui/frontend && git add public/data
git commit -m "data: refresh memorial export"
git push                                  # Vercel auto-rebuilds
```

---

## Data Pipeline

Ten-stage async pipeline. Stages 1–4 and 10 are checkpointed to SQLite; stages 5–9 are deterministic in-memory transforms.

```
Discover → Fetch → Triage → Extract → Dedup → Merge → Sanity → Quality → Reconcile → Export
```

| Stage | What it does |
|-------|-------------|
| Discover | Query each source for candidate URLs |
| Fetch | Pull HTML + clean text (Playwright for JS-rendered sources) |
| Triage | Cheap LLM pre-filter — classifies articles as homicide/attempted/other before full extraction |
| Extract | Gemini structured extraction → `ExtractedArticleData` (incl. multi-victim explode) |
| Dedup | Blocking on `(city, YYYY-MM)` → Jaro-Winkler pre-filter → multilingual cosine gate |
| Merge | Fold cluster into one `CanonicalCaseSchema` with conflict tracking |
| Sanity | Date clamping, script purity, three-axis legal status, per-category confidence |
| Quality | Status synonyms, name conflict → aliases, evidence dedup, canonical ID |
| Reconcile | Cross-case consistency and provenance attribution |
| Export | Schema 2.0 single-run JSON (`output/{run_id}.json`) |

**Side subsystems:**

- **Media pipeline** — per-case image harvest, download, classification (keyword → CLIP → Gemini cascade), perceptual-hash dedup across sources, and split into decorative `media` vs evidentiary `media_evidence`.
- **Enrichment** — second-pass over an existing canonical case: generates targeted multilingual queries and additively merges new findings.

## Sources

| Source | Language | Priority | Notes |
|--------|----------|----------|-------|
| `police` | Hebrew | 0 | police.gov.il press releases |
| `ynet` | Hebrew | 1 | Mainstream Israeli news |
| `walla` | Hebrew | 1 | Commercial news — closes Bedouin coverage gap |
| `makan` | Arabic | 2 | Kan-affiliated Arabic public broadcaster |
| `panet` | Arabic | 2 | Arabic SPA — requires Playwright |
| `google_news` | Mixed | — | Google News RSS aggregator with language detection |

Source priority is used as a tie-breaker when selecting a canonical record within a cluster and as a confidence weight during merge.

## Data Model

Two main schemas (Pydantic v2, in [`crime_pipeline/models.py`](crime_pipeline/models.py)):

- **`ExtractedArticleData`** — per-article LLM output. Multilingual victim names (`victim_name_ar`/`he`/`en` + `aliases`), three-axis suspect status (`suspect_status` physical / `legal_status` / `police_investigation_status`), evidence and media inventories.
- **`CanonicalCaseSchema`** — merged case across sources. `conflicts` map (field → {source_url: value}); `flags` audit trail (e.g. `date_year_corrected`, `mixed_script_name_quarantined`, `single_source`, `needs_tier_2`); `confidence` per-category dict (`case_identity` 25%, `victim_identity` 20%, `timeline` 15%, `legal_status` 15%, `location_detail` 15%, `media` 10%) rolled up into `confidence_score`; `media[*].mirror_urls` (other publishers hosting the same perceptual-hash-matched image) and `media[*].appearance_count` (cross-publisher corroboration count).

ORM tables (`RawArticle`, `ExtractedRecord`, `CanonicalCase`) checkpoint each stage to SQLite at `data/pipeline.db`.

## Requirements

- Python ≥ 3.11
- Node.js ≥ 18 (for the frontend)
- A Google Gemini API key (for the pipeline)
- Playwright Chromium (only needed for the Panet scraper)

## Setup

```bash
# 1. Clone
git clone git@github.com:FadiBadarni/israel-homicides.git
cd israel-homicides

# 2. Pipeline
python -m venv .venv
. .venv/Scripts/activate          # Windows  (Linux/macOS: . .venv/bin/activate)
pip install -e .
pip install -e ".[vision]"        # optional: CLIP classifier (~1 GB)
playwright install chromium       # optional: needed for Panet scraper

# 3. Configure secrets
cp .env.example .env              # set GEMINI_API_KEY=...

# 4. Frontend
cd ui/frontend
npm install
```

## Usage

### Canonical build (production)

```bash
# Re-extract if prompts changed
python -m crime_pipeline --reextract-all

# Build the canonical dataset for a date window
python -m crime_pipeline --build-canonical \
    --date-from 2026-01-01 --date-to 2026-12-31
# → writes output/canonical_2026-01-01_2026-12-31.json
```

### Full pipeline run (discover new articles)

```bash
python -m crime_pipeline \
    --query "Arraba 2026" \
    --sources ynet,police,panet \
    --date-from 2026-01-01 \
    --date-to 2026-12-31
```

### Run only specific stages

Each stage checkpoints to SQLite, so you can rerun a subset against previously-fetched data:

```bash
python -m crime_pipeline --query "Arraba 2026" \
    --stage extract --stage dedup --stage merge --stage export
```

### Pipeline funnel diagnostic

```bash
python -m crime_pipeline --show-pipeline-funnel kw_ar_    # by prefix
python -m crime_pipeline --show-pipeline-funnel all        # everything
```

### Second-pass enrichment

```bash
python -m crime_pipeline --enrich-case output/case.json
python -m crime_pipeline --enrich-case output/case.json --arabic-only
python -m crime_pipeline --enrich-case output/case.json --tier 2
```

Enrichment is **additive** — it never overwrites high-confidence existing data; it fills gaps and accumulates corroborating sources.

### CLI flags

```
--query                          Search query (required unless --build-canonical / --enrich-case)
--build-canonical                Build canonical dataset from existing DB state
--reextract-all                  Re-extract every triage-passed article
--enrich-case <path>             Run enricher against an existing canonical JSON
--sources                        Comma-separated: ynet,police,panet,walla,makan
--date-from / --date-to          ISO dates
--max-per-source                 Per-source discovery cap (default: 50)
--stage <name>                   Repeatable. Limits run to listed stages.
--jaro-threshold                 Override name-similarity gate (default: 0.88)
--cosine-threshold               Override embedding-similarity gate (default: 0.82)
--show-pipeline-funnel <prefix>  Diagnostic: show article drop-off per stage
--tier 1|2|3                     Enrichment-only: target source tier
--arabic-only                    Enrichment-only: Arabic locale + queries
--log-level                      DEBUG|INFO|WARNING|ERROR
--run-id                         Custom run ID (auto-generated when omitted)
```

## Post-processing passes

Three deterministic, zero-API-cost cleanup stages run inline after merge:

- **Sanity** — clamps dates to publication-year ± 2, enforces script purity (Arabic/Hebrew/Latin name fields), normalises discovery sources to real publishers, splits legacy single-axis `police_status` into the three-axis model, and computes per-category confidence scores.
- **Quality** — collapses status synonyms (`arrested` ≡ `in_custody`), promotes mixed-script name conflicts to `aliases`, deduplicates evidence items, and generates a phonetic `canonical_case_id`.
- **Reconcile** — cross-case consistency and per-field provenance attribution.

None of these passes discard data — they correct field placement and improve signal clarity.

## Configuration

All settings live in `.env` (loaded via `pydantic-settings`). Defaults in [`crime_pipeline/config.py`](crime_pipeline/config.py).

| Variable               | Default                | Purpose                              |
| ---------------------- | ---------------------- | ------------------------------------ |
| `GEMINI_API_KEY`       | —                      | Required for extraction + enrichment |
| `LLM_MODEL`            | `gemini-2.5-flash`     | Gemini model id                      |
| `LLM_MAX_TOKENS`       | `1024`                 | Per-response token cap               |
| `LLM_CONCURRENCY`      | `8`                    | Max concurrent LLM requests          |
| `JARO_THRESHOLD`       | `0.88`                 | Name pre-filter gate                 |
| `COSINE_THRESHOLD`     | `0.82`                 | Embedding decision gate              |
| `ROBOTS_TXT_RESPECT`   | `true`                 | Honor robots.txt                     |
| `REQUEST_DELAY_SECONDS`| `3.0`                  | Politeness delay between fetches     |
| `DB_PATH`              | `data/pipeline.db`     | SQLite checkpoint location           |

## Project Layout

```
crime_pipeline/
├── __main__.py            # Click CLI entry point
├── pipeline.py            # Ten-stage async orchestrator
├── config.py              # Settings (pydantic-settings)
├── models.py              # SQLAlchemy ORM + Pydantic schemas
├── scrapers/              # Source-specific discover/fetch
│   ├── base.py            # Abstract scraper + robots.txt
│   ├── ynet.py, walla.py, makan.py, panet.py, police.py
│   ├── google_news.py     # Google News RSS aggregator
│   └── tier_registry.py   # Three-tier source classification
├── extraction/            # LLM extraction + multi-victim explode
├── dedup/                 # Embeddings + Jaro + DuckDB graph
├── merging/               # Cluster → canonical case + conflict resolution
├── enrichment/            # Sanity, quality, reconcile + second-pass enricher
├── media/                 # Harvest → download → classify → dedup
├── export/                # Schema 2.0 JSON export
├── storage/               # SQLAlchemy engine + repository helpers
└── utils/                 # Gazetteer, hashing, retry
ui/
├── frontend/              # Next.js memorial frontend
│   ├── app/               # App Router pages (home, case detail, contribute)
│   ├── components/        # React components (count-up, language toggle, etc.)
│   ├── lib/               # API client, i18n, regions, formatting
│   └── public/data/       # Static JSON exported from pipeline
└── api/                   # FastAPI backend (dev convenience only)
scripts/                   # Data export, sweeps, maintenance
data/
├── gazetteer.json         # City/region reference (3-script + coords)
└── pipeline.db            # (gitignored) SQLite checkpoint DB
output/                    # (gitignored) per-run canonical JSON
tests/
```

## Development

### Pipeline

```bash
pip install -e . pytest ruff mypy

pytest                                    # all tests
pytest tests/test_media_pipeline.py -v    # media subsystem only
ruff check crime_pipeline                 # lint
mypy crime_pipeline                       # type check

python scripts/demo_media_real.py         # media demo — no DB or API key needed
```

Tests cover Harvester, Classifier, Dedup, Splitter, and the Pipeline orchestrator end-to-end. No network I/O — `MediaDownloader` is monkeypatched with deterministic hashes. No `pytest-asyncio` required.

### Frontend

```bash
cd ui/frontend
npm run dev                               # dev server on port 3000
npm run build                             # production build
python scripts/export_static_data.py      # refresh public/data/ from SQLite
```

## License

This project documents publicly reported information for memorial purposes.
