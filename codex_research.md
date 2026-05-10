# Codex — Discover Phase Research

## Findings

1. **Stage ordering: use three distinct stages, not one `cleanup`.** The current orchestrator treats stages as named resumable DAG nodes: the default set is explicit (`discover`, `fetch`, `extract`, `dedup`, `merge`, `export`) and `pipeline_start` logs the sorted selected stages ([pipeline.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/pipeline.py:87), [pipeline.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/pipeline.py:89)). The control flow then gates each stage by name; even `dedup` and `merge` share an implementation block but remain distinct selectors ([pipeline.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/pipeline.py:123)). So add `sanity -> quality -> reconcile` between `merge` and `export`, giving nine stage names. This mirrors dbt's `--select` pattern for subsets of DAG nodes and Dagster/Prefect's convention that meaningful retry/observability boundaries are separate ops/tasks, with dependencies carried by data flow.

2. **`reconcile_cases()` should return a small dataclass.** Current `reconcile_file(path, ...) -> dict` mixes file I/O, mutation, and summary construction ([reconciler.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/reconciler.py:114), [reconciler.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/reconciler.py:122)). Split to:

   ```python
   @dataclass(slots=True)
   class ReconcileResult:
       cases: list[dict[str, Any]]
       merged_pairs: list[dict[str, Any]]
       cases_before: int
       cases_after: int
   ```

   This is cleaner than Option X because no hidden mutable stats state, and cleaner than Option Y because named fields survive future audit additions without tuple-position churn. `reconcile_file()` can remain a wrapper that loads JSON, calls `reconcile_cases()`, writes if not dry-run, and returns `result.summary_dict()` for CLI compatibility.

3. **CLI ergonomics: extend `--stage` choices.** The Click surface already uses a repeatable `--stage` with `click.Choice([...])` and constructs a `stage_set`; no negative per-stage flags exist ([__main__.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/__main__.py:133), [__main__.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/__main__.py:136), [__main__.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/__main__.py:283)). Add `sanity`, `quality`, `reconcile` to both the `Choice` list and default set ([__main__.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/__main__.py:286)). `--no-sanity / --no-quality / --no-reconcile` creates a second selection dialect and contradiction cases. If opt-out ergonomics are later needed, prefer one repeatable `--skip-stage click.Choice(...)`, not three booleans.

4. **Merged-pair audit: do both, with different payload sizes.** Existing stats are accumulated in `self.stats`, embedded into the export envelope, and logged at pipeline completion ([pipeline.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/pipeline.py:129), [pipeline.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/pipeline.py:136), [pipeline.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/pipeline.py:638)). Existing detailed candidate pairs already live in stats as `review_pair_details` ([pipeline.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/pipeline.py:389)). For reconcile, put counts and `reconcile_audit_path` in stats, log an INFO summary, and write full `merged_pairs` to `output/{run_id}_reconcile_audit.jsonl`. The reconciler already produces pair records and logs pair events ([reconciler.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/reconciler.py:181), [reconciler.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/reconciler.py:189)); the side-file keeps durable row-level audit without bloating `pipeline_complete`.

5. **Schema field shapes.** Add explicit Pydantic fields, not `extra="allow"`, because `CanonicalCaseSchema` is the persisted/exported contract ([models.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/models.py:307)). Actual produced shapes:

   ```python
   class TierCoverage(BaseModel):
       tier_1: list[str] = Field(default_factory=list)
       tier_2: list[str] = Field(default_factory=list)
       tier_3: list[str] = Field(default_factory=list)
       untiered: list[str] = Field(default_factory=list)

   class TimelineEvent(BaseModel):
       date: str
       event: str
       confidence: Literal["low", "medium", "high"] = "high"
       source_url: Optional[str] = None

   tier_coverage: TierCoverage = Field(default_factory=TierCoverage)
   timeline: list[TimelineEvent] = Field(default_factory=list)
   motive_translations: list[str] = Field(default_factory=list)
   ```

   `tier_coverage` is exactly `{"tier_1": [], "tier_2": [], "tier_3": [], "untiered": []}` populated with publisher strings ([sanity_pass.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/sanity_pass.py:647), [sanity_pass.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/sanity_pass.py:659)). `timeline` is a list of dicts with `date`, `event`, `confidence`, and optional `source_url` ([sanity_pass.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/sanity_pass.py:683), [sanity_pass.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/sanity_pass.py:768)). `motive_translations` is a list of strings and is removed when empty today ([quality_pass.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/quality_pass.py:601), [quality_pass.py](C:/Users/fadi_/Desktop/crime/crime_pipeline/enrichment/quality_pass.py:631)).

Architecture references: dbt node selection (`https://docs.getdbt.com/reference/node-selection/syntax`), Dagster op graphs (`https://legacy-versioned-docs.dagster.dagster-docs.io/concepts/ops-jobs-graphs/graphs`), Prefect tasks (`https://docs.prefect.io/v3/concepts/tasks`).
