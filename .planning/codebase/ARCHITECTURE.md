# Architecture

**Analysis Date:** 2026-05-12

## Pattern Overview

**Overall:** Linear staged pipeline with SQLite checkpointing and in-memory deterministic cleanup

**Key Characteristics:**
- Ten sequential stages; stages 1–4 + 10 persist to SQLite; stages 5–9 are in-memory
- Every stage is independently resumable via `--stage` CLI flag; persisted stages pull from DB when skipped
- Async throughout (`asyncio`) but CPU-bound work offloaded to `asyncio.to_thread`
- Single `Pipeline` object orchestrates the entire run; stateful `stats` dict accumulates metrics
- Two storage backends: SQLite (durable checkpoint) + DuckDB (transient dedup graph)

## Pipeline Stages

```
Stage 1: Discover    (async, persisted)   → DiscoveredUrl list
Stage 2: Fetch       (async, persisted)   → RawArticle rows in SQLite
Stage 2.5: Triage    (async, LLM)         → filters to homicide/attempted only (~90% drop)
Stage 3: Extract     (async, LLM)         → ExtractedRecord rows in SQLite
         Relevance   (sync, in-memory)    → drops zero-signal extractions
         Explode     (sync, in-memory)    → multi-victim articles → N virtual records
Stage 4: Dedup       (sync, DuckDB)       → clusters + singletons + review_pairs
Stage 5: Merge       (sync, in-memory)    → CanonicalCaseSchema per cluster
Stage 6: Sanity      (sync, in-memory)    → date clamp, script purity, confidence
Stage 7: Quality     (sync, in-memory)    → status synonyms, name conflicts, evidence dedup
Stage 8: Reconcile   (sync, in-memory)    → cross-cluster merge on name similarity
Stage 9: Media       (async, per-case)    → harvest + classify + dedup images
Stage 10: Export     (sync)               → output/{run_id}.json (Schema 2.0)
```

## Layers

**CLI Layer:**
- Purpose: Argument parsing, mode dispatch, logging setup
- Location: `crime_pipeline/__main__.py`
- Contains: `cli()` click command; mode dispatch for `--cities`, `--keyword-mode`, `--enrich-case`, `--verify-truth`, `--reconcile`, `--show-pipeline-funnel`
- Depends on: `Pipeline`, `Settings`, mode-specific modules
- Used by: End users / operators

**Configuration Layer:**
- Purpose: Environment-based settings with validation
- Location: `crime_pipeline/config.py`
- Contains: `Settings` pydantic-settings class; reads from `.env`
- Pattern: `Settings()` called once at CLI startup; passed into `Pipeline.__init__()`

**Pipeline Orchestrator:**
- Purpose: Wires all ten stages; owns `stats` dict; manages `run_id`
- Location: `crime_pipeline/pipeline.py`
- Contains: `Pipeline` class with one method per stage (`_discover`, `_fetch`, `_triage`, `_extract`, `_dedup_and_merge`, `_run_cleanup`, `_export`)
- Depends on: All subsystem modules
- Used by: CLI layer

**Scraper Layer:**
- Purpose: URL discovery + article fetching per news source
- Location: `crime_pipeline/scrapers/`
- Contains: `BaseScraper` abstract class; concrete `YnetScraper`, `Arab48Scraper`, `IsraelhayomScraper`; `SCRAPER_REGISTRY` dict; `TierRegistry`
- Pattern: `get_scraper(source_name)` factory; each scraper implements `discover()` + `fetch()`
- Depends on: `httpx`, `beautifulsoup4`, `trafilatura`, `playwright` (panet only)
- Used by: `Pipeline._discover()`, `Pipeline._fetch()`

**Extraction Layer:**
- Purpose: LLM-based structured data extraction from article text
- Location: `crime_pipeline/extraction/`
- Contains: `ArticleExtractor` (Gemini async client), `Triager` (cheap pre-filter), `prompts.py` (700+ line system prompt), `validator.py` (JSON repair + Pydantic validation), `multivictim.py` (explode multi-victim articles), `relevance.py` (post-extract signal gate), `triage.py`
- Depends on: `google-genai`, `pydantic`
- Used by: `Pipeline._triage()`, `Pipeline._extract()`

**Deduplication Layer:**
- Purpose: Cross-source article deduplication via two-gate similarity
- Location: `crime_pipeline/dedup/`
- Contains: `Deduplicator` (orchestrator), `ArticleEmbedder` (`paraphrase-multilingual-MiniLM-L12-v2`), `DeduplicationGraph` (DuckDB union-find), `name_normalizer.py` (romanization + Jaro-Winkler)
- Pattern: Blocking → Jaro-Winkler (pre-filter, 0.88) → Cosine (decision gate, 0.82)
- Depends on: `sentence-transformers`, `duckdb`, `jellyfish`, `anyascii`
- Used by: `Pipeline._dedup_and_merge()`

**Merging Layer:**
- Purpose: Collapse a dedup cluster into a single canonical case
- Location: `crime_pipeline/merging/`
- Contains: `CaseMerger`, `ConflictResolver` (per-field rules: priority / age / status-machine / boolean-OR / count-max)
- Used by: `Pipeline._dedup_and_merge()`

**Enrichment Layer:**
- Purpose: Post-merge deterministic cleanup + optional second-pass LLM enrichment
- Location: `crime_pipeline/enrichment/`
- Contains: `sanity_pass.py` (date clamp, script purity, confidence), `quality_pass.py` (semantic cleanup), `reconciler.py` (cross-cluster merge), `enricher.py` (LLM second pass, opt-in), `provenance.py` (per-field source attribution)
- Pattern: `sanity_pass` MUST run before `quality_pass` (invariant #5 in CLAUDE.md)
- Used by: `Pipeline._run_cleanup()`, CLI `--enrich-case` mode

**Media Layer:**
- Purpose: Image harvest, classification (keyword → CLIP → Gemini Vision), dedup, evidence/decorative split
- Location: `crime_pipeline/media/`
- Contains: `MediaPipeline` (orchestrator), `MediaHarvester` (og:, JSON-LD, lazy-img), `MediaDownloader` (async + URL-hash cache), `MediaClassifier` (3-tier cascade), `MediaSplitter` (evidence vs decorative), `MediaDedup` (sha256 + phash union-find)
- Pattern: Single `MediaPipeline` instance per pipeline run (CLIP model loaded once)
- Depends on: `open-clip-torch` (optional), `pillow`, `beautifulsoup4`
- Used by: `Pipeline._attach_media()`

**Storage Layer:**
- Purpose: SQLite persistence (engine, session factory, upsert helpers)
- Location: `crime_pipeline/storage/`
- Contains: `db.py` (engine + WAL mode + `init_db()`), `repository.py` (upsert/fetch helpers)
- Pattern: Caller controls `session.commit()`; `SessionLocal` is `None` until `init_db()` called
- Used by: `Pipeline` (all persisted stages)

**Export Layer:**
- Purpose: Write final Schema 2.0 JSON
- Location: `crime_pipeline/export/json_exporter.py`
- Contains: `JSONExporter.export_run()` — single `{run_id}.json` per run

**Utils:**
- Location: `crime_pipeline/utils/`
- `gazetteer.py` — lazy city lookup: Hebrew/Arabic/English → `CityRecord` (district + region)
- `hashing.py` — `url_hash` / `text_hash` / `short_hash` (SHA-256 prefix)
- `retry.py` — `http_retry` (3×, 2–8 s) + `llm_retry` (4×, 4–60 s) tenacity decorators

## Data Flow

**Normal Pipeline Run:**

1. CLI parses args → instantiates `Settings` + `Pipeline`
2. `Pipeline.run()` calls `_discover()` → scrapers return `DiscoveredUrl` list
3. `_fetch()` → scrapers return `ArticleResult`; saved to `raw_articles` SQLite table
4. `_triage()` → Gemini-flash classifies title+lede; ~90% dropped; verdicts written to `raw_articles`
5. `_extract()` → Gemini extracts structured JSON; saved to `extracted_records` SQLite table
6. `_filter_relevance()` → drops zero-signal extractions in-memory
7. `explode_extraction()` → multi-victim articles produce N virtual records
8. `Deduplicator.run()` → blocking → Jaro → cosine → returns clusters + singletons + review_pairs
9. `CaseMerger.merge_cluster()` per cluster → `CanonicalCaseSchema` Pydantic objects
10. `_run_cleanup()` → sanity → quality → reconcile (all in-memory, zero LLM cost)
11. `_persist_canonical_cases()` → saves final cases to `canonical_cases` SQLite table
12. `_export()` → `JSONExporter.export_run()` → `output/{run_id}.json`

**State Management:**
- `Pipeline.stats` dict accumulates counts across all stages; included in export
- Run-scoped via `pipeline_run_id` on all SQLite rows — multi-city/keyword runs share one DB
- In-memory lists passed between stages within a single `asyncio.run()` call

## Key Abstractions

**`CanonicalCaseSchema` (Pydantic):**
- Purpose: The authoritative output record for a single homicide case
- Location: `crime_pipeline/models.py`
- Fields: victim names (3 scripts), dates, city, weapon, suspect info, legal status (3-axis), confidence_score, sources, media, evidence, aliases, flags, conflicts

**`ExtractedArticleData` (Pydantic):**
- Purpose: LLM extraction output schema; input to dedup/merge
- Location: `crime_pipeline/models.py`
- Validates script purity, date formats, enum values

**`BaseScraper` (ABC):**
- Purpose: Contract for all news source scrapers
- Location: `crime_pipeline/scrapers/base.py`
- Pattern: Subclass, implement `discover()` + `fetch()`, register in `SCRAPER_REGISTRY`

**`Settings` (pydantic-settings):**
- Purpose: Single configuration object passed through entire pipeline
- Location: `crime_pipeline/config.py`
- Instantiated once; CLI can mutate `jaro_threshold` / `cosine_threshold` before pipeline starts

## Entry Points

**CLI:**
- Location: `crime_pipeline/__main__.py`
- Invocation: `python -m crime_pipeline` or `crime-pipeline` (installed script)
- Modes: normal run, `--cities`, `--keyword-mode`, `--enrich-case`, `--verify-truth`, `--reconcile`, `--show-pipeline-funnel`

**Pipeline API:**
- Location: `crime_pipeline/pipeline.py::Pipeline.run()`
- Called by: CLI after settings/pipeline instantiation

## Error Handling

**Strategy:** Log and continue at scraper/article level; raise at pipeline/settings level

**Patterns:**
- Scraper errors: `log.error("fetch_error", ...)` + `stats["fetch_failed"] += 1`; pipeline continues
- LLM extraction failures: failure sentinel row written to `extracted_records` with `extraction_status="failed"`; counted in stats
- Media pipeline errors: `log.warning("media_pipeline_error", ...)` inside `try/except`; case still exported
- Settings load failure: `sys.exit(2)` with error message
- `KeyboardInterrupt`: caught in CLI, `sys.exit(130)`
- Strict city/date filter: non-matching cases logged + dropped; gazetteer miss → keep + flag

## Cross-Cutting Concerns

**Logging:** `structlog` everywhere; `log = structlog.get_logger()` at module level; ISO timestamps; structured fields (not string interpolation)

**Validation:** Pydantic v2 throughout; `ExtractedArticleData` re-validates each extraction before merge; `CanonicalCaseSchema` re-validated after cleanup round-trip

**Script Purity:** Enforced in `extraction/prompts.py` (LLM instruction) + `enrichment/sanity_pass.py` (post-extraction fix); `victim_name_ar` Arabic-only, `victim_name_he` Hebrew-only, `victim_name_en` Latin-only

**Dedup Thresholds (immutable defaults):**
- Jaro-Winkler gate: 0.88 (pre-filter only; never sole merge trigger)
- Cosine merge gate: 0.82
- Cosine review zone: 0.70–0.82 (→ `review_pairs`, never auto-merged)

**Run Isolation:** All SQLite rows carry `pipeline_run_id`; resume and multi-city backfills scope queries by run_id to prevent cross-contamination

---

*Architecture analysis: 2026-05-12*
