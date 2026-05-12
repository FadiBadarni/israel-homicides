# CLAUDE.md — 12-rule template

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work.

## Rule 1 — Think Before Coding
State assumptions explicitly. Ask rather than guess.
Push back when a simpler approach exists. Stop when confused.

## Rule 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No abstractions for single-use code.

## Rule 3 — Surgical Changes
Touch only what you must. Don't improve adjacent code.
Match existing style. Don't refactor what isn't broken.

## Rule 4 — Goal-Driven Execution
Define success criteria. Loop until verified.
Strong success criteria let Claude loop independently.

## Rule 5 — Use the model only for judgment calls
Use for: classification, drafting, summarization, extraction.
Do NOT use for: routing, retries, deterministic transforms.
If code can answer, code answers.

## Rule 6 — Token budgets are not advisory
Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.

## Rule 7 — Surface conflicts, don't average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.

## Rule 8 — Read before you write
Before adding code, read exports, immediate callers, shared utilities.
If unsure why existing code is structured a certain way, ask.

## Rule 9 — Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

## Rule 10 — Checkpoint after every significant step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.

## Rule 11 — Match the codebase's conventions, even if you disagree
Conformance > taste inside the codebase.
If you think a convention is harmful, surface it. Don't fork silently.

## Rule 12 — Fail loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.

---

# Developer Context for israel-homicides

## Development commands

```bash
# Install (core dependencies)
pip install -e .

# Install CLIP vision classifier (~1 GB: torch + ViT-B-32 weights)
pip install -e ".[vision]"

# Install Playwright browser (required for panet scraper)
playwright install chromium

# Run tests
pytest
pytest tests/test_media_pipeline.py -v     # specific file, verbose

# Lint / type-check
ruff check crime_pipeline
mypy crime_pipeline

# PRODUCTION CANONICAL BUILD — the operating mode that ships data
# Step 1 (only after a prompt change): re-extract every triage-passed article
python -m crime_pipeline --reextract-all
# Step 2: build the single canonical dataset for a date window
python -m crime_pipeline --build-canonical \
    --date-from 2026-01-01 --date-to 2026-02-16
# → writes output/canonical_2026-01-01_2026-02-16.json
# No discover/fetch. Operates on the DB's current state. Global dedup,
# declarative filter on incident_geography + window. Idempotent.

# Full pipeline run (development / discover new articles)
python -m crime_pipeline \
    --query "Arraba 2026" \
    --sources ynet,police,panet \
    --date-from 2026-01-01 --date-to 2026-12-31

# Resume from a specific stage (uses existing DB rows)
python -m crime_pipeline --query "Arraba 2026" \
    --stage extract --stage dedup --stage merge --stage export

# Pipeline funnel diagnostic — where did articles drop in a sweep?
python -m crime_pipeline --show-pipeline-funnel kw_ar_           # prefix
python -m crime_pipeline --show-pipeline-funnel all              # everything
python -m crime_pipeline --show-pipeline-funnel kw_ --funnel-format=jsonl

# Second-pass enrichment on an existing canonical case
python -m crime_pipeline --enrich-case output/<run_id>.json

# Media subsystem demo — real Channel 13 + Mako + Ynet HTML, no DB or API key
python scripts/demo_media_real.py
```

---

## Architecture

Ten stages. Stages 1–4 + 10 are checkpointed to SQLite (`data/pipeline.db`); stages
5–9 are deterministic in-memory transforms. Any stage can be skipped via
`--stage`; for the persisted stages this pulls inputs from the DB, for the
in-memory cleanup stages it simply no-ops.

```
Discover → Fetch → Triage → Extract → Dedup → Merge → Sanity → Quality → Reconcile → Export
```

**Triage** is a cheap LLM pre-filter (Gemini 2.5-flash, thinking disabled) that
classifies each fetched article as homicide/attempted_homicide/other before
the expensive full-extraction stage. ~90% of articles are dropped here at
zero downstream cost.

**Multi-victim extraction** (added 2026-05): when one article describes
multiple named victims (triple murders, week-in-review summaries like
"13 قتيلا منذ بدء العام"), the LLM populates `additional_victims` on the
extraction. Between extract and dedup, `_explode_multivictim` flattens the
extraction into N+1 virtual per-victim records (composite IDs
`ext_id#victim_index`). The dedup stage enforces same-article exclusion so
the N records from one article never merge with each other but each can
still merge with cross-source articles about the same individual.

The last three (Sanity, Quality, Reconcile) are **deterministic, zero-API-cost
cleanup stages** that previously only ran inside `--enrich-case` mode. Default
pipeline runs were silently producing exports without script-purity correction,
three-axis legal-status splitting, date repair, or per-category confidence
calibration. They now run inline by default.

`--enrich-case <json>` remains a separate operator-driven mode. It re-runs
discover→fetch→extract on case-derived queries and additively merges new
sources. This is per-case Gemini-API-burning work — kept opt-in by design.

---

## Module map

| File | Responsibility |
|------|---------------|
| `pipeline.py` | Ten-stage async orchestrator (incl. multi-victim explode between extract and dedup) |
| `diagnostics.py` | `--show-pipeline-funnel` SQL counts: discover → fetch → triage → extract per (run_id, source) |
| `config.py` | `pydantic-settings` — reads `.env` |
| `models.py` | SQLAlchemy ORM tables + Pydantic v2 schemas |
| `scrapers/base.py` | Abstract `BaseScraper` (discover + fetch + robots.txt via httpx — urllib's robotparser silently fails behind Cloudflare) |
| `scrapers/ynet.py` | Hebrew server-rendered news |
| `scrapers/walla.py` | Hebrew commercial news. Added 2026-05 to close the Bedouin femicide gap (Basma Abu Freiha and similar cases Ynet covered without naming). Discovery via Google News RSS; body via `<article>` selector (JSON-LD `articleBody` is empty on Walla template) |
| `scrapers/makan.py` | Arabic public broadcaster (Kan-affiliated). Closes Bedouin/Negev coverage gap. Discovery via Google News RSS, body via `.article-content` |
| `scrapers/police.py` | police.gov.il press releases |
| `scrapers/panet.py` | Arabic SPA — requires Playwright shared browser |
| `scrapers/google_news.py` | Google News RSS aggregator; language detection via Unicode blocks |
| `scrapers/tier_registry.py` | Three-tier source classification; per-field tier preferences; coverage-gap flags |
| `extraction/extractor.py` | Async Gemini client; one auto-retry on validation failure |
| `extraction/multivictim.py` | Pure-function explode: 1 multi-victim extraction → N+1 virtual records |
| `extraction/prompts.py` | System + user prompt builders (700+ lines of extraction rules incl. MULTI-VICTIM RULE) |
| `extraction/validator.py` | JSON repair → Pydantic validation → retry-prompt builder |
| `dedup/deduplicator.py` | Blocking → Jaro (pre-filter) → cosine (decision gate) orchestrator |
| `dedup/embedder.py` | `paraphrase-multilingual-MiniLM-L12-v2`; batch encode, L2-normalised |
| `dedup/graph.py` | DuckDB union-find; `bulk_save_edges` in single txn (avoids SQLite O(n²) bottleneck) |
| `dedup/name_normalizer.py` | `romanize_name` + Arabic diacritic stripping + jellyfish Jaro-Winkler |
| `merging/merger.py` | Cluster → `CanonicalCaseSchema` with conflict map + weighted confidence |
| `merging/conflict_resolver.py` | Per-field rules: priority / age / status-machine / boolean-OR / count-max |
| `enrichment/enricher.py` | Query gen → discover → fetch → extract → additive merge |
| `enrichment/sanity_pass.py` | Systemic bug fixes: date clamping, script purity, source normalisation, three-axis legal status, per-category confidence |
| `enrichment/quality_pass.py` | Semantic cleanup: status synonyms, name conflicts → aliases, evidence dedup, canonical_case_id |
| `enrichment/provenance.py` | Per-field source attribution + role assignment (initial_report, arrest_status, etc.) |
| `media/pipeline.py` | Harvest → download → classify → dedup → split orchestrator |
| `media/harvester.py` | Extracts candidates from HTML: og:, JSON-LD, figure, lazy-img, gallery, video |
| `media/downloader.py` | Async download; URL-hash cache; sha256 + phash + dims |
| `media/classifier.py` | Keyword → CLIP → Gemini Vision cascade (tier 3 is a stub — see invariants) |
| `media/splitter.py` | Evidence vs decorative routing: 8 promotion rules + 3 demotion rules |
| `media/dedup.py` | Within-article sha256 collapse + cross-source phash/CLIP union-find |
| `export/json_exporter.py` | Schema 2.0 single-run JSON; backward-compat Schema 1.0 helpers |
| `storage/db.py` | Engine + WAL mode + `SessionLocal` factory (`init_db` must be called first) |
| `storage/repository.py` | Upsert/fetch helpers — caller controls `session.commit()` |
| `utils/gazetteer.py` | Lazy city lookup: 3-script + aliases → `CityRecord` (district + region) |
| `utils/hashing.py` | `url_hash` / `text_hash` / `short_hash` (SHA-256 prefix) |
| `utils/retry.py` | `http_retry` (3×, 2–8 s) + `llm_retry` (4×, 4–60 s) tenacity decorators |

---

## Key invariants — do not break

1. **Jaro-Winkler is a pre-filter, never the sole merge trigger.** Cosine similarity (Gate 2) is the
   decision gate. A pair that passes Jaro but fails cosine must NOT be merged.

2. **Script purity.** `victim_name_ar` must contain only Arabic characters (U+0600–06FF);
   `victim_name_he` only Hebrew (U+0590–05FF); `victim_name_en` only Latin (A–Z). Mixed-script
   values must go to `aliases`, never to a named script field. Enforced by both `prompts.py` and
   `sanity_pass.py`.

3. **Year inference from article date, never current date.** The LLM must derive a missing year
   from the article's publication date ± 2 years. This is spelled out in `extraction/prompts.py`
   and re-validated in `enrichment/sanity_pass.py`.

4. **Enrichment is additive.** `enricher.py` fills null fields and accumulates sources but never
   overwrites an existing high-confidence value.

5. **`sanity_pass` runs before `quality_pass`.** `quality_pass` assumes script purity has already
   been applied. Reversing the order produces incorrect results.

6. **Cosine zone 0.70–0.82 → `review_pairs`.** Articles in this zone are flagged for human review;
   they must not be auto-merged. Widening `cosine_threshold` past 0.82 absorbs this zone silently.

7. **Gemini Vision (media tier 3) is an intentional stub.**
   `classifier.py:155–162` — the budget check fires but the API call is not implemented. The
   evidence string `gemini_skipped:budget_or_offline` is expected, not an error.

8. **CLIP requires the `.[vision]` extra.** If `open-clip-torch` is absent, the classifier records
   `clip:unavailable` (graceful degradation — not a failure).

9. **`storage/db.py::SessionLocal` is `None` until `init_db()` is called.** Never import
   `SessionLocal` directly at module level; always access it via `db.SessionLocal` after init.

---

## Source priority

`police (0) > ynet (1) > panet (2)` — used in `dedup/deduplicator.py::select_canonical()` as a
tiebreak and in `merging/merger.py` as an inverse confidence weight.

---

## Dedup thresholds (defaults in `config.py`, overridable via CLI)

| Gate | Threshold | Role |
|------|-----------|------|
| Jaro-Winkler | 0.88 | Pre-filter — must pass before cosine is evaluated |
| Cosine | 0.82 | Decision gate — sufficient to merge when Jaro passes or a name is absent |
| Cosine (review zone) | 0.70–0.82 | → `review_pairs` for human adjudication |

---

## Media classifier tiers

| Tier | Package | Cost | Status |
|------|---------|------|--------|
| Keyword | built-in | free | always active; bilingual (Hebrew + Arabic) substring maps |
| CLIP | `open-clip-torch` (optional `.[vision]`) | free after load | lazy-loaded ViT-B-32 laion2b |
| Gemini Vision | `google-genai` | paid, 15 calls/case budget | **stub** — wired but not implemented |

---

## Per-category confidence (populated by `sanity_pass`)

| Category | Weight | What it measures |
|----------|--------|-----------------|
| `case_identity` | 25% | Incident confirmed; boosted by ≥ 2 independent sources |
| `victim_identity` | 20% | Name fields coverage; boosted by multilingual confirmation or aliases |
| `timeline` | 15% | `incident_date` + `death_date` coverage; penalised if dates are backwards |
| `legal_status` | 15% | Three-axis status completeness; boosted by cross-tier corroboration |
| `location_detail` | 15% | City/neighborhood/district/region/place_type/hospital; capped at 0.7 without Tier 2 |
| `media` | 10% | Image count (0 → 0.05, 1 → 0.4, 2–3 → 0.7, 4+ → 0.75+) |

`confidence_score` = weighted rollup of the six categories.

---

## Output format — Schema 2.0

Single file `output/{run_id}.json`:

```json
{
  "schema_version": "2.0",
  "kind": "crime_pipeline.run",
  "pipeline_run_id": "...",
  "exported_at": "...",
  "run": { "started_at", "finished_at", "duration_seconds", "stages_executed" },
  "stats": { "discovered", "fetched", "extracted", "clusters", "cases_exported", ... },
  "case_count": 1,
  "cases": [ { ...CanonicalCaseSchema... } ],
  "human_summary": "..."
}
```

Backward-compat Schema 1.0 helpers (`export_manifest`, `export_summary`) still exist in
`export/json_exporter.py` and produce `*_manifest.json` / `*_summary.txt` side-files.

---

## Common tasks

### Add a new source scraper
1. Subclass `BaseScraper` in `scrapers/newsource.py`; implement `discover()` + `fetch()`
2. Register in `scrapers/__init__.py::SCRAPER_REGISTRY`
3. Add tier in `scrapers/tier_registry.py`
4. Update `--sources` help text in `__main__.py`

### Add a new extraction field
1. Add to `ExtractedArticleData` in `models.py`
2. Update JSON schema block in `extraction/prompts.py`
3. Add merge/conflict rule in `merging/merger.py` (and `conflict_resolver.py` if needed)
4. Mirror in `CanonicalCaseSchema` in `models.py`
5. Add sanity/quality rule in the relevant pass if the field needs post-processing

### Adjust dedup thresholds without breaking invariants
- Use `--jaro-threshold` / `--cosine-threshold` CLI flags for one-off runs
- Lower cosine threshold expands the merge zone and silently absorbs review_pairs — check graph edge counts before committing to a new default

### Debug an extraction failure
1. Check `ExtractedRecord.extraction_status` and `validation_status` in the DB
2. Look at `extracted_json` (first attempt raw response stored there even on failure)
3. Set `--log-level DEBUG` to see full LLM prompt/response interaction

### Debug a deduplication decision
- Inspect DuckDB edges in `DeduplicationGraph` (in-memory `:memory:` by default in pipeline runs)
- Check `review_pairs` in the dedup result for the ambiguous zone

---

## Frontend — Memorial Map

The frontend (`ui/frontend/`) is a single-page Next.js memorial. There is no list view,
no filter sidebar, no review queue. The map is the index.

Key files:
- `app/page.tsx` — the only route; fetches `/api/memorial` and renders the layout.
- `components/memorial-map.tsx` — left/right layout shell, year filter, URL sync.
- `components/israel-map.tsx` — pure SVG of Israel; locality dots positioned via lat/lng projection.
- `components/bloom-card.tsx` — right-side panel: locality state → case state.
- `components/year-scrubber.tsx`, `components/death-count.tsx` — peripheral chrome.
- `lib/project.ts` — equirectangular projection + the Israel-outline polygon coordinates.

Backend dependency:
- `GET /api/memorial` aggregates the latest run's `died` cases by locality, attaching
  `lat`/`lng` from the gazetteer. Cases without a gazetteer match are counted in
  `unresolved_count` and excluded.

Tests:
- Backend: `pytest tests/test_memorial_endpoint.py tests/test_gazetteer_coords.py`
- Frontend smoke: `cd ui/frontend && npm run test:e2e` (Playwright; auto-starts dev server, stubs the API)

Start order:
```bash
uvicorn ui.api.main:app --reload --port 8001   # backend
cd ui/frontend && npm run dev                  # frontend (port 3000)
```

---

## Test patterns

- **No `pytest-asyncio` required** — `tests/conftest.py` wraps async tests with `asyncio.run` fallback
- **No network I/O in tests** — `MediaDownloader.fetch_many` is monkeypatched; sha256/phash values are injected deterministically
- **Coverage**: Harvester (og:, JSON-LD, lazy-load, blocklists, scoped to `<article>`), Classifier (caption-name match, stock domains, Arabic keywords), Dedup (sha256 collapse, phash clustering, canonical selection), Splitter (evidence promotion/demotion rules, low-confidence stock), Pipeline e2e (cross-publisher portrait dedup, og:image corroboration)

```bash
pytest                                          # all tests
pytest tests/test_media_pipeline.py::TestSplitter -v   # one class
pytest -s                                       # show stdout
```
