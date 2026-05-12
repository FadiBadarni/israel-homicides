# External Integrations

**Analysis Date:** 2026-05-12

## APIs & External Services

**LLM (Google Gemini):**
- Google Gemini API — article triage (cheap pre-filter) + full structured extraction
  - SDK/Client: `google-genai >= 0.5`; client instantiated as `genai.Client(api_key=...)` in `crime_pipeline/extraction/extractor.py:44`
  - Auth: `GEMINI_API_KEY` env var (required; checked before pipeline starts in `crime_pipeline/__main__.py:754`)
  - Model default: `gemini-2.5-flash` (configurable via `LLM_MODEL` env var)
  - Triage: `thinking_config={"thinking_budget": 0}` — thinking disabled to reduce cost
  - Extraction: `response_mime_type="application/json"` — native JSON mode
  - Concurrency: `asyncio.Semaphore(8)` default; configurable via `LLM_CONCURRENCY`
  - Retry: `llm_retry` tenacity decorator (4×, 4–60 s backoff) in `crime_pipeline/utils/retry.py`

**News Sources (scraped, not API-based):**
- Ynet (`ynet.co.il`) — Hebrew server-rendered news; scraper at `crime_pipeline/scrapers/ynet.py`
- Arab48 (`arab48.com`) — Arabic news; scraper at `crime_pipeline/scrapers/arab48.py`
- Israel Hayom (`israelhayom.co.il`) — scraper at `crime_pipeline/scrapers/israelhayom.py`
- Google News RSS — URL aggregator via `googlenewsdecoder`; scraper at `crime_pipeline/scrapers/_gnews.py`

**Removed / Legacy Scrapers (still referenced in CLAUDE.md but not in registry):**
- `panet.com` — SPA; `crime_pipeline/scrapers/panet.py` exists but not registered in `scrapers/__init__.py::SCRAPER_REGISTRY`
- `police.gov.il` — `crime_pipeline/scrapers/police.py` exists but not registered

## Data Storage

**Databases:**
- SQLite — primary persistence for all pipeline checkpoints
  - Connection: `DB_PATH` env var; default `data/pipeline.db`
  - Client: SQLAlchemy ORM (`sqlalchemy`); WAL mode + foreign keys enabled via `crime_pipeline/storage/db.py:_enable_wal_mode()`
  - Tables: `raw_articles`, `extracted_records`, `canonical_cases` (defined in `crime_pipeline/models.py`)
  - Session: `crime_pipeline/storage/db.py::SessionLocal` (module-level; `None` until `init_db()` called)
  - Schema migration: inline `ALTER TABLE` in `crime_pipeline/storage/db.py::_apply_additive_migrations()`

- DuckDB — in-memory dedup edge graph (`:memory:` default)
  - Client: `duckdb` package; used in `crime_pipeline/dedup/graph.py`
  - Purpose: union-find for dedup clustering; avoids SQLite O(n²) write bottleneck on large batches

**File Storage:**
- Local filesystem only
  - Output JSON: `output/{run_id}.json` (Schema 2.0)
  - Reconcile audit: `output/{run_id}_reconcile_audit.jsonl`
  - Verify output: `{run_file}.verify.json`
  - Media image cache: `.cache/media/{url_hash}.bin`
  - Ground-truth JSONL: `data/truth_*.jsonl` (manually maintained)

**Caching:**
- URL-hash media cache at `.cache/media/` — `crime_pipeline/media/downloader.py`; sha256 + phash + dims stored as `.bin` files

## Authentication & Identity

**Auth Provider:**
- No user auth — CLI tool only
- Single secret: `GEMINI_API_KEY` in `.env` file or shell environment

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry or equivalent)

**Logs:**
- `structlog` structured console logs; ISO timestamps; color output in dev
- Log level: `INFO` default; `--log-level DEBUG` shows full LLM prompt/response
- Configuration in `crime_pipeline/__main__.py:configure_logging()`

**Pipeline Diagnostics:**
- `--show-pipeline-funnel` CLI flag — SQL count queries per stage per run_id via `crime_pipeline/diagnostics.py`; outputs table or JSONL

**Verification:**
- `--verify-truth` + `--verify-run` flags — precision/recall/F1 against a ground-truth JSONL; `crime_pipeline/verification.py`

## CI/CD & Deployment

**Hosting:**
- None — local batch CLI tool; no server deployment

**CI Pipeline:**
- None detected (no `.github/workflows/`, no CI config)

## External HTTP Calls (Scrapers)

**Outbound requests:**
- All via `httpx[http2]`; HTTP/2 enabled
- Robots.txt respected by default (`ROBOTS_TXT_RESPECT=true`); checked in `crime_pipeline/scrapers/base.py::can_fetch()`
- Rate limiting: `REQUEST_DELAY_SECONDS=3.0` default; configurable
- Retry: `http_retry` tenacity decorator (3×, 2–8 s) in `crime_pipeline/utils/retry.py`
- Playwright Chromium: used for JavaScript-heavy pages (panet.com SPA)

## Environment Configuration

**Required env vars:**
- `GEMINI_API_KEY` — required for `triage` and `extract` pipeline stages

**Optional env vars (all have defaults):**
- `DB_PATH` — default `data/pipeline.db`
- `LLM_MODEL` — default `gemini-2.5-flash`
- `LLM_MAX_TOKENS` — default `1024`
- `LLM_CONCURRENCY` — default `8`
- `JARO_THRESHOLD` — default `0.88`
- `COSINE_THRESHOLD` — default `0.72` (config.py default; CLAUDE.md docs say 0.82 — use config.py as truth)
- `ROBOTS_TXT_RESPECT` — default `true`
- `REQUEST_DELAY_SECONDS` — default `3.0` (in code; not in .env.example)

**Secrets location:**
- `.env` file at project root (gitignored); template at `.env.example`

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None — all requests are scraper pull operations

---

*Integration audit: 2026-05-12*
