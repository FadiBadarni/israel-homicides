# Codex (Round 2) — Refined position

I stay on C, but not the minimal “just call three functions” version.

Sonnet’s schema gotcha is directionally correct but technically misstated. I verified `CanonicalCaseSchema(**result)` does not fail: Pydantic ignores extras by default. That means `motive_translations`, `tier_coverage`, source `tier`, etc. are silently dropped on rehydration/export unless the schema is extended or `extra="allow"` is set. The enricher avoids this by mutating plain dicts loaded from JSON. This does not push me to A or B; it means C must include an explicit schema/export fix, not an accidental dict-to-model round trip.

On stage shape, I disagree with Claude/Opus’s “inside merge loop” framing. Sanity/quality should be a batch post-merge stage over case dicts before DB persistence/export. Per-case cleanup inside `_dedup_and_merge` couples merger internals to postprocessing and still cannot host reconcile cleanly. A batch stage also lets quality recompute tier coverage after all source/media normalization.

On reconciler placement, I prefer in-memory before final export, not Sonnet’s `reconcile_file` after export. Post-export is easy but creates a transient wrong artifact and leaves DB rows/stats less coherent. Refactor the reconciler core to accept `list[dict]`; keep `reconcile_file` as a CLI wrapper.

On reconciler safety, Gemini is right that fragmented UI records are a real downstream quality problem. But Claude/Opus’s “new and undocumented” risk is also fair. Make reconcile default-on but opt-out, with `merged_pairs` in stats and a dry-run command for audits.

On tier-aware re-querying: surface gaps, do not auto-fetch. `run_sanity_pass` already computes `tier_coverage` and `needs_tier_*` flags via `coverage_gaps`. The main pipeline should emit those flags; enrichment should consume them only when the operator chooses `--enrich-all` or a target tier.
