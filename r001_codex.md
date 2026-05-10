# Codex (Round 1) — Pick: C
## Architectural argument
Pick C: run sanity, quality, and reconcile inline; keep enrichment opt-in. The current orchestrator is a checkpointed linear dataflow, with default stages hard-coded as `discover/fetch/extract/dedup/merge/export` in `crime_pipeline/pipeline.py:77-87` and the actual post-merge/export boundary at `pipeline.py:123-134`. Sanity, quality, and reconciliation are transformations over already-materialized cases, so they fit naturally after merge and before final export without changing upstream scrape/extract semantics.

Enrichment is different. `CaseEnricher` opens an output JSON, generates new case-derived queries, discovers/fetches/extracts new articles, and mutates the case (`crime_pipeline/enrichment/enricher.py:793-842`, `enricher.py:882-936`). That is a feedback loop from canonical output back to acquisition. Folding it into every main run would make the pipeline no longer a single pass over a bounded query/source/date input.

## Failure-mode analysis (what gets worse, what gets better)
Better: every normal output gets deterministic invariants, not only enriched outputs. Today `run_sanity_pass` and `run_quality_pass` are called only inside enrichment (`enricher.py:811-833`), while the main export filters and writes cases directly (`pipeline.py:571-643`). Inline reconcile also addresses known fragmentation from dedup blocking; the reconciler explicitly targets cases split by missing city/date and uses conservative no-conflict gates (`crime_pipeline/enrichment/reconciler.py:4-13`, `reconciler.py:167-217`).

Worse: automatic reconcile can create false merges. But its blast radius is bounded to case JSON, can be dry-run/checkpointed, and its criteria are auditable via `merged_pairs` (`reconciler.py:247-261`). Automatic enrichment has much worse failure modes: query drift, cross-incident contamination, quota exhaustion, rate-limit latency, and non-deterministic source availability. The code already needs identity gating before additive merge (`enricher.py:446-607`), which is evidence that this loop is risky enough to remain explicit.

## Coupling cost
C has low coupling. It adds case-to-case and case-normalization stages after merge. A would couple canonical schema, tier policy, Google News scraping, Gemini extraction, and export timing into one control loop. Tier logic is valuable (`crime_pipeline/scrapers/tier_registry.py:122-158`, `tier_registry.py:248-264`), but tier-targeted re-querying belongs behind an operator flag because it changes acquisition scope and cost.

## Specific file/stage changes implied by your pick
Add stages `sanity`, `quality`, `reconcile`, then `export`. Extend `Pipeline.run` defaults and CLI `--stage` choices (`crime_pipeline/__main__.py:132-140`, `__main__.py:282-287`). Persist post-merge case checkpoints before export. Refactor reconciler to operate on in-memory cases as well as files. Keep `--enrich-case`, `--tier`, Arabic-only, query/article limits as separate CLI controls (`__main__.py:60-105`, `__main__.py:202-247`).
