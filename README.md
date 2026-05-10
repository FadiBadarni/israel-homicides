# israel-homicides

Multi-source Arabic/Hebrew crime news scraping and AI extraction pipeline. Discovers homicide news articles across the currently enabled Hebrew and Arabic news sources, runs LLM-based structured extraction, deduplicates across sources, and produces one canonical case record per incident.

## Overview

The pipeline runs as six checkpointed stages, each persisting to SQLite so any stage can be skipped or resumed:

```
Discover → Fetch → Extract → Dedup → Merge → Export
```

| Stage     | What it does                                                              |
| --------- | ------------------------------------------------------------------------- |
| Discover  | Query each source for candidate URLs                                       |
| Fetch     | Pull HTML + clean text (Playwright for JS-rendered sources)                |
| Extract   | Gemini structured extraction → `ExtractedArticleData`                      |
| Dedup     | Blocking on `(city, YYYY-MM)` → Jaro-Winkler name pre-filter → multilingual cosine gate |
| Merge     | Fold cluster of articles into one `CanonicalCaseSchema` with conflict tracking |
| Export    | Schema 2.0 single-run JSON (`output/{run_id}.json`) + backward-compat manifest + summary |

Side subsystems:

- **Media pipeline** — per-case image harvest, download, classification (keyword → CLIP → Gemini cascade), perceptual-hash dedup across sources, and split into decorative `media` vs evidentiary `media_evidence`.
- **Enrichment** — second-pass over an existing canonical case: generates targeted multilingual queries (victim names in Arabic/Hebrew/English, neighborhood, suspect relation) and additively merges new findings.

## Sources

| Source       | Language | Priority | Notes                                  |
| ------------ | -------- | -------- | -------------------------------------- |
| `ynet`       | Hebrew   | 1        | Mainstream Israeli news                |
| `arab48`     | Arabic   | 2        | Arabic local press                     |

Source priority is used both as a tie-breaker when selecting a canonical record within a cluster and as a confidence weight during merge.

## Data Model

Two main schemas (Pydantic v2, in [`crime_pipeline/models.py`](crime_pipeline/models.py)):

- **`ExtractedArticleData`** — per-article LLM output. Multilingual victim names (`victim_name_ar`/`he`/`en` + `aliases`), three-axis suspect status (`suspect_status` physical / `legal_status` / `police_investigation_status`), evidence and media inventories.
- **`CanonicalCaseSchema`** — merged case across sources. `conflicts` map (field → {source_url: value}); `flags` audit trail (e.g. `date_year_corrected`, `mixed_script_name_quarantined`, `single_source`, `needs_tier_2`); `confidence` per-category dict (`case_identity` 25%, `victim_identity` 20%, `timeline` 15%, `legal_status` 15%, `location_detail` 15%, `media` 10%) rolled up into `confidence_score`; `media[*].mirror_urls` (other publishers hosting the same perceptual-hash-matched image) and `media[*].appearance_count` (cross-publisher corroboration count).

ORM tables (`RawArticle`, `ExtractedRecord`, `CanonicalCase`) checkpoint each stage to SQLite at `data/pipeline.db`.

## Requirements

- Python ≥ 3.11
- A Google Gemini API key
- Playwright browsers are only needed for legacy/local experiments that use Playwright-based scrapers.

## Setup

```bash
# 1. Clone
git clone git@github.com:FadiBadarni/israel-homicides.git
cd israel-homicides

# 2. Create venv + install
python -m venv .venv
. .venv/Scripts/activate          # Windows
# . .venv/bin/activate              # Linux/macOS
pip install -e .

# Optional: CLIP vision classifier (~1 GB: torch + ViT-B-32 weights)
pip install -e ".[vision]"

# 3. Install Playwright browsers
playwright install chromium

# 4. Configure secrets
cp .env.example .env
# edit .env and set GEMINI_API_KEY=...
```

## Usage

### Full pipeline run

```bash
python -m crime_pipeline \
    --query "Arraba 2026" \
    --sources ynet,arab48 \
    --date-from 2026-01-01 \
    --date-to 2026-12-31
```

Exit codes: `0` if any cases or extractions were produced, `2` if the run completed but produced nothing usable, `130` on Ctrl-C.

### Run only specific stages

Each stage checkpoints to SQLite, so you can rerun a subset against previously-fetched data:

```bash
python -m crime_pipeline --query "Arraba 2026" \
    --stage extract --stage dedup --stage merge --stage export
```

### Second-pass enrichment

Run targeted enrichment on an existing canonical case JSON:

```bash
# Default mixed locale
python -m crime_pipeline --enrich-case output/arraba_real_002_canonical.json

# Arabic-only queries
python -m crime_pipeline --enrich-case output/arraba_real_002_canonical.json --arabic-only

# Tier-2 Arabic local press only
python -m crime_pipeline --enrich-case output/arraba_real_002_canonical.json --tier 2
```

Enrichment is **additive** — it never overwrites high-confidence existing data; it fills gaps and accumulates corroborating sources.

### CLI flags

```
--query                          Search query (required unless --enrich-case)
--enrich-case <path>             Run enricher against an existing canonical JSON
--sources                        Comma-separated: ynet,arab48 (default)
--date-from / --date-to          ISO dates
--max-per-source                 Per-source discovery cap (default: 50)
--stage <name>                   Repeatable. Limits run to listed stages.
--jaro-threshold                 Override name-similarity gate (default: 0.88)
--cosine-threshold               Override embedding-similarity gate (default: 0.82)
--tier 1|2|3                     Enrichment-only: target source tier
--arabic-only                    Enrichment-only: Arabic locale + queries
--log-level                      DEBUG|INFO|WARNING|ERROR
--run-id                         Custom run ID (auto-generated when omitted)
```

## Post-processing passes

Two cleanup passes run automatically after enrichment, inside the enricher:

- **`sanity_pass`** — fixes systemic extraction bugs: clamps dates to publication-year ± 2, enforces script purity (Arabic/Hebrew/Latin fields), normalises `googlenews` discovery sources to real publishers + tier, splits the legacy single-axis `police_status` into the three-axis model, and computes per-category confidence scores.
- **`quality_pass`** — semantic cleanup: collapses status synonyms (`arrested` ≡ `in_custody`), promotes name conflicts that are just script variants to `aliases`, deduplicates evidence items reported in two languages into one multilingual record, and generates a phonetic `canonical_case_id` via romanization.

Neither pass discards data — they correct field placement and improve signal clarity.

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
├── pipeline.py            # Six-stage orchestrator
├── config.py              # Settings (pydantic-settings)
├── models.py              # SQLAlchemy ORM + Pydantic schemas
├── scrapers/              # Source-specific discover/fetch
│   ├── base.py
│   ├── ynet.py
│   ├── arab48.py
│   └── tier_registry.py
├── extraction/            # LLM extraction + JSON-schema validation
├── dedup/                 # Embeddings + Jaro + DuckDB graph
├── merging/               # Cluster → canonical case + conflict resolution
├── enrichment/            # Second-pass query gen + additive merge
├── media/                 # Image harvest + classify + phash dedup
├── export/                # JSON + manifest + human summary
├── storage/               # SQLAlchemy engine + repository helpers
└── utils/                 # Gazetteer, hashing, retry
data/
├── gazetteer.json         # Static city/region reference
└── pipeline.db            # (gitignored) SQLite checkpoint DB
output/                    # (gitignored) per-run cases + manifests
tests/
```

## Development

```bash
# Install with dev tools
pip install -e . pytest ruff mypy

# Run tests
pytest
pytest tests/test_media_pipeline.py -v   # media subsystem only

# Lint
ruff check crime_pipeline

# Type check
mypy crime_pipeline

# Media demo (real Channel 13 + Mako + Ynet HTML — no DB or API key needed)
python scripts/demo_media_real.py
```

`tests/test_media_pipeline.py` contains 30+ unit and integration tests covering Harvester, Classifier, Dedup, Splitter, and the Pipeline orchestrator end-to-end. No network I/O — `MediaDownloader` is monkeypatched with deterministic hashes. No `pytest-asyncio` required; `tests/conftest.py` wraps async tests with an `asyncio.run` fallback.

## License

[MIT](LICENSE)
