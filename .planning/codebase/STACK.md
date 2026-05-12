# Technology Stack

**Analysis Date:** 2026-05-12

## Languages

**Primary:**
- Python 3.11+ — entire codebase; `requires-python = ">=3.11"` in `pyproject.toml`

**Secondary:**
- None — pure Python project

## Runtime

**Environment:**
- CPython 3.11+ (type hints use 3.11 syntax: `str | None`, `tuple[str, ...]`)
- Windows compatible (UTF-8 stdout/stderr workaround in `crime_pipeline/__main__.py:31-34`)

**Package Manager:**
- pip + hatchling build backend (`pyproject.toml`)
- Lockfile: not present (no `requirements.txt` or `poetry.lock`)

## Frameworks

**Core:**
- `asyncio` (stdlib) — all pipeline stages are async; `asyncio.run()` at CLI entry
- `click` — CLI argument parsing; entry point `crime-pipeline` in `pyproject.toml`
- `pydantic >= 2.0` — all data schemas (`models.py`, `config.py`, `media/settings.py`)
- `pydantic-settings >= 2.0` — `Settings` class in `crime_pipeline/config.py` reads from `.env`
- `sqlalchemy` — ORM for SQLite persistence; declarative base in `crime_pipeline/models.py`
- `alembic` — schema migration (declared dependency; runtime migrations done inline via `_apply_additive_migrations` in `crime_pipeline/storage/db.py`)

**LLM / AI:**
- `google-genai >= 0.5` — Gemini API client; used in `crime_pipeline/extraction/extractor.py` and `crime_pipeline/extraction/triage.py`; model default `gemini-2.5-flash`
- `sentence-transformers` — multilingual embedding model `paraphrase-multilingual-MiniLM-L12-v2`; loaded in `crime_pipeline/dedup/embedder.py`

**Scraping / HTTP:**
- `httpx[http2]` — async HTTP client for all scrapers
- `playwright` — headless Chromium for JavaScript-heavy SPA scrapers (panet.com); requires `playwright install chromium`
- `beautifulsoup4` + `lxml` — HTML parsing in scrapers and media harvester
- `trafilatura` — article text extraction from raw HTML

**Deduplication:**
- `duckdb` — in-memory union-find graph storage in `crime_pipeline/dedup/graph.py`; avoids SQLite O(n²) write bottleneck
- `jellyfish` — Jaro-Winkler string similarity in `crime_pipeline/dedup/name_normalizer.py`
- `anyascii` — Arabic/Hebrew romanization for name normalization

**Testing:**
- `pytest` — test runner; config in `pyproject.toml`
- No `pytest-asyncio` required — `tests/conftest.py` wraps async tests with `asyncio.run`

**Build/Dev:**
- `ruff` — linting + import sorting; config in `pyproject.toml` (`target-version = "py311"`, `line-length = 100`)
- `mypy` — strict type checking; config in `pyproject.toml` (`strict = true`)
- `hatchling` — build backend

**Logging:**
- `structlog` — structured logging throughout; configured in `crime_pipeline/__main__.py:configure_logging()`

**Utilities:**
- `tenacity` — HTTP and LLM retry decorators in `crime_pipeline/utils/retry.py`
- `langdetect` — language detection fallback in scrapers
- `googlenewsdecoder >= 0.1.7` — Google News RSS URL decoding in `crime_pipeline/scrapers/_gnews.py`
- `python-dotenv` — `.env` file loading at CLI startup
- `jsonschema` — JSON validation
- `structlog` — structured console logging

## Optional Dependencies

**Vision extra (`.[vision]`):**
- `open-clip-torch >= 2.24.0` — CLIP image classifier (`ViT-B-32 laion2b`); ~1 GB download; lazy-loaded in `crime_pipeline/media/classifier.py`
- `pillow >= 10.0.0` — image processing for perceptual hashing and CLIP input

## Key Dependencies

**Critical:**
- `google-genai` — all LLM calls (triage + extraction); requires `GEMINI_API_KEY` env var
- `sentence-transformers` — dedup cosine gate; downloads model on first use (~400 MB)
- `playwright` — panet scraper only; chromium install separate step

**Infrastructure:**
- `sqlalchemy` + `duckdb` — two storage layers; SQLite for persistence, DuckDB for dedup graph
- `httpx` — all HTTP scraping; HTTP/2 enabled

## Configuration

**Environment:**
- All settings via `crime_pipeline/config.py::Settings` (pydantic-settings)
- `.env` file loaded automatically; example at `.env.example`
- Key required var: `GEMINI_API_KEY`
- Optional overrides: `DB_PATH`, `LLM_MODEL`, `LLM_MAX_TOKENS`, `LLM_CONCURRENCY`, `JARO_THRESHOLD`, `COSINE_THRESHOLD`, `ROBOTS_TXT_RESPECT`

**Build:**
- `pyproject.toml` — single source of truth for project metadata, deps, ruff, mypy config

## Platform Requirements

**Development:**
- Python 3.11+
- `pip install -e .` for core deps
- `pip install -e ".[vision]"` for CLIP (~1 GB)
- `playwright install chromium` for panet scraper

**Production:**
- No server required — runs as a CLI batch process
- SQLite DB at `data/pipeline.db` (auto-created)
- Output JSON at `output/{run_id}.json`

---

*Stack analysis: 2026-05-12*
