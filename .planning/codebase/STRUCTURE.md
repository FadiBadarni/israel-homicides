# Codebase Structure

**Analysis Date:** 2026-05-12

## Directory Layout

```
crime/                              # project root
├── crime_pipeline/                 # main package
│   ├── __init__.py
│   ├── __main__.py                 # CLI entry point (click)
│   ├── pipeline.py                 # 10-stage async orchestrator
│   ├── config.py                   # pydantic-settings Settings class
│   ├── models.py                   # SQLAlchemy ORM + Pydantic v2 schemas
│   ├── diagnostics.py              # --show-pipeline-funnel SQL queries
│   ├── verification.py             # precision/recall/F1 truth-vs-pipeline
│   ├── scrapers/                   # news source adapters
│   │   ├── __init__.py             # SCRAPER_REGISTRY + get_scraper() factory
│   │   ├── base.py                 # BaseScraper ABC, ArticleResult, DiscoveredUrl
│   │   ├── ynet.py                 # Ynet (Hebrew) scraper
│   │   ├── arab48.py               # Arab48 (Arabic) scraper
│   │   ├── israelhayom.py          # Israel Hayom scraper
│   │   ├── panet.py                # Panet SPA scraper (Playwright; not registered)
│   │   ├── _gnews.py               # Google News RSS helper
│   │   └── tier_registry.py        # 3-tier source domain classification
│   ├── extraction/                 # LLM extraction subsystem
│   │   ├── extractor.py            # Gemini async client + batch extract
│   │   ├── triage.py               # cheap pre-filter (Gemini-flash, thinking off)
│   │   ├── prompts.py              # 700+ line system + user prompt builders
│   │   ├── validator.py            # JSON repair → Pydantic validation + retry prompt
│   │   ├── multivictim.py          # explode 1 extraction → N virtual per-victim records
│   │   └── relevance.py            # post-extract homicide signal gate
│   ├── dedup/                      # deduplication subsystem
│   │   ├── deduplicator.py         # Blocking → Jaro → Cosine orchestrator
│   │   ├── embedder.py             # paraphrase-multilingual-MiniLM-L12-v2, L2-normalised
│   │   ├── graph.py                # DuckDB union-find; bulk_save_edges
│   │   └── name_normalizer.py      # romanize_name, Arabic diacritic strip, Jaro-Winkler
│   ├── merging/                    # cluster → canonical case
│   │   ├── merger.py               # CaseMerger; conflict map + weighted confidence
│   │   └── conflict_resolver.py    # per-field rules (priority/age/status-machine/bool-OR)
│   ├── enrichment/                 # post-merge cleanup + optional second-pass
│   │   ├── sanity_pass.py          # date clamp, script purity, confidence calibration
│   │   ├── quality_pass.py         # status synonyms, name conflicts → aliases, evidence dedup
│   │   ├── reconciler.py           # cross-cluster merge on name similarity
│   │   ├── enricher.py             # opt-in LLM second pass (--enrich-case)
│   │   └── provenance.py           # per-field source attribution + role assignment
│   ├── media/                      # image harvest/classify/dedup subsystem
│   │   ├── __init__.py             # re-exports ArticleContext, MediaPipeline, MediaSettings
│   │   ├── pipeline.py             # harvest → download → classify → dedup → split
│   │   ├── harvester.py            # og:, JSON-LD, figure, lazy-img, gallery, video
│   │   ├── downloader.py           # async download; URL-hash cache; sha256+phash+dims
│   │   ├── classifier.py           # Keyword → CLIP → Gemini Vision cascade
│   │   ├── splitter.py             # evidence vs decorative routing (8 promotion + 3 demotion rules)
│   │   ├── dedup.py                # sha256 collapse + phash/CLIP union-find
│   │   ├── hashing.py              # perceptual hash utilities
│   │   ├── models.py               # media-specific Pydantic schemas
│   │   └── settings.py             # MediaSettings pydantic model
│   ├── export/
│   │   └── json_exporter.py        # Schema 2.0 export_run(); Schema 1.0 compat helpers
│   ├── storage/
│   │   ├── db.py                   # SQLAlchemy engine, WAL mode, init_db(), SessionLocal
│   │   └── repository.py           # save_article, save_extraction, get_all_extractions, etc.
│   └── utils/
│       ├── gazetteer.py            # normalize_city(); 3-script city lookup → CityRecord
│       ├── hashing.py              # url_hash, text_hash, short_hash (SHA-256 prefix)
│       └── retry.py                # http_retry (3×, 2–8s) + llm_retry (4×, 4–60s)
├── tests/                          # pytest test suite
│   ├── conftest.py                 # asyncio.run wrapper; shared fixtures
│   ├── test_media_pipeline.py      # media subsystem tests (Harvester, Classifier, Dedup, Splitter)
│   ├── test_pipeline_cleanup_stages.py
│   ├── test_dedup_*.py             # dedup-specific tests
│   ├── test_arab48_*.py            # Arab48 scraper tests
│   ├── test_verify_*.py            # verification/truth tests
│   └── test_*.py                   # ~35 test files total
├── scripts/
│   ├── demo_media_real.py          # media subsystem demo (no DB/API key needed)
│   └── ingest_sources.py           # utility script
├── data/
│   ├── pipeline.db                 # SQLite database (auto-created; gitignored)
│   └── truth_*.jsonl               # ground-truth homicide records (manually maintained)
├── output/                         # pipeline output JSON files (gitignored except .gitkeep)
│   └── .gitkeep
├── .cache/
│   └── media/                      # image download cache (.bin files; gitignored)
├── pyproject.toml                  # build config, deps, ruff, mypy
├── .env                            # secrets (gitignored)
├── .env.example                    # env var template
└── CLAUDE.md                       # developer context and architecture docs
```

## Directory Purposes

**`crime_pipeline/scrapers/`:**
- Purpose: One file per news source; all share `BaseScraper` contract
- Contains: `SCRAPER_REGISTRY` dict maps string name → class; `get_scraper()` factory
- Key files: `base.py` (ABC), `__init__.py` (registry), `tier_registry.py` (domain → tier 1/2/3)
- Note: `panet.py` exists but is NOT in `SCRAPER_REGISTRY`; requires Playwright

**`crime_pipeline/extraction/`:**
- Purpose: Everything LLM-related for turning article text into structured data
- Contains: Gemini client, prompt builders, Pydantic validation, multi-victim explode
- Key files: `extractor.py` (main client), `prompts.py` (all LLM instructions), `triage.py` (pre-filter)

**`crime_pipeline/dedup/`:**
- Purpose: Detect duplicate articles describing the same incident across sources
- Contains: Two-gate similarity pipeline; multilingual embeddings; DuckDB graph
- Key files: `deduplicator.py` (orchestrator), `embedder.py` (sentence-transformers)

**`crime_pipeline/enrichment/`:**
- Purpose: Deterministic post-merge cleanup (always runs) + opt-in LLM second pass
- Key files: `sanity_pass.py` (runs first), `quality_pass.py` (runs second), `reconciler.py`
- Invariant: `sanity_pass` before `quality_pass` — never reverse the order

**`crime_pipeline/media/`:**
- Purpose: Image extraction, classification, and deduplication per case
- Key files: `pipeline.py` (orchestrator), `classifier.py` (3-tier cascade), `splitter.py` (evidence routing)
- Note: CLIP classifier is optional (requires `.[vision]` extra); Gemini Vision tier is a stub

**`crime_pipeline/storage/`:**
- Purpose: Database abstraction; all raw SQL hidden behind repository functions
- Pattern: Import `db` module, access `db.SessionLocal` (never `from storage.db import SessionLocal` at module level)

**`tests/`:**
- Purpose: pytest unit + integration tests; ~35 files
- No network I/O (monkeypatched); no `pytest-asyncio` (conftest wraps with `asyncio.run`)

## Key File Locations

**Entry Points:**
- `crime_pipeline/__main__.py`: CLI (`python -m crime_pipeline` or `crime-pipeline`)
- `crime_pipeline/pipeline.py`: `Pipeline.run()` — main orchestration logic

**Configuration:**
- `crime_pipeline/config.py`: `Settings` class (all env vars)
- `.env.example`: template for required + optional env vars
- `pyproject.toml`: build metadata, dependencies, ruff config, mypy config

**Core Data Schemas:**
- `crime_pipeline/models.py`: `RawArticle` ORM, `ExtractedRecord` ORM, `CanonicalCaseSchema` Pydantic, `ExtractedArticleData` Pydantic

**Source Registry:**
- `crime_pipeline/scrapers/__init__.py`: `SCRAPER_REGISTRY` dict
- `crime_pipeline/scrapers/tier_registry.py`: domain → tier 1/2/3 classification

**Ground Truth:**
- `data/truth_*.jsonl`: manually curated JSONL with `{city, victim_name_he, victim_name_ar, incident_date}`

## Naming Conventions

**Files:**
- Snake_case: `deduplicator.py`, `json_exporter.py`, `name_normalizer.py`
- Prefix with underscore for internal helpers: `crime_pipeline/scrapers/_gnews.py`

**Directories:**
- Short lowercase nouns: `scrapers/`, `extraction/`, `dedup/`, `merging/`, `enrichment/`, `media/`, `storage/`, `export/`, `utils/`

**Classes:**
- PascalCase: `ArticleExtractor`, `Deduplicator`, `CaseMerger`, `BaseScraper`

**Functions:**
- Snake_case; private methods prefixed with `_`: `_discover()`, `_run_cleanup()`

**Run IDs:**
- Auto-generated: `str(uuid.uuid4())[:12]`
- City mode: `{city_slug}_{year}_{source}` (e.g., `arraba_2026_arab48`)
- Keyword mode: `kw_{lang}_{md5_8chars}_{year}` (e.g., `kw_ar_abc12345_2026`)

## Where to Add New Code

**New Source Scraper:**
1. Create `crime_pipeline/scrapers/{newsource}.py` — subclass `BaseScraper`; implement `discover()` + `fetch()`
2. Register in `crime_pipeline/scrapers/__init__.py::SCRAPER_REGISTRY`
3. Add tier entry in `crime_pipeline/scrapers/tier_registry.py`
4. Update `--sources` help text in `crime_pipeline/__main__.py`
5. Add tests in `tests/test_{newsource}_*.py`

**New Extraction Field:**
1. Add to `ExtractedArticleData` in `crime_pipeline/models.py`
2. Update JSON schema block in `crime_pipeline/extraction/prompts.py`
3. Add merge/conflict rule in `crime_pipeline/merging/merger.py` (and `conflict_resolver.py`)
4. Mirror in `CanonicalCaseSchema` in `crime_pipeline/models.py`
5. Add sanity/quality rule in `crime_pipeline/enrichment/sanity_pass.py` or `quality_pass.py`

**New Cleanup Rule:**
- Date/script/confidence fixes: `crime_pipeline/enrichment/sanity_pass.py`
- Semantic cleanup (status synonyms, name conflicts): `crime_pipeline/enrichment/quality_pass.py`
- Cross-case merging: `crime_pipeline/enrichment/reconciler.py`

**New Utility:**
- Shared helpers: `crime_pipeline/utils/`

**New Tests:**
- Location: `tests/test_{feature}.py`
- Pattern: No network I/O (monkeypatch); wrap async with `asyncio.run` (see `tests/conftest.py`)

## Special Directories

**`data/`:**
- Purpose: SQLite DB + ground-truth JSONL files
- Generated: `pipeline.db` is auto-created
- Committed: `truth_*.jsonl` files are committed; `pipeline.db` is gitignored

**`output/`:**
- Purpose: Pipeline output JSON per run
- Generated: Yes (by `JSONExporter`)
- Committed: No (`.gitkeep` only)

**`.cache/media/`:**
- Purpose: Image download cache; URL hash → binary file
- Generated: Yes
- Committed: No (gitignored)

**`.planning/codebase/`:**
- Purpose: Codebase analysis documents for GSD planning tools
- Generated: By mapping agents
- Committed: Yes

---

*Structure analysis: 2026-05-12*
