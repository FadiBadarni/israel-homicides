# Gemini — Discover Phase Research

## 1. dbt-style 'tests vs models'
**Ecosystem Pattern:** dbt separates **models** (transformation) from **tests** (assertions) via `.sql` vs `.yml` files. Tests run *after* the model is materialized (`dbt test`). 
**Application:** In this pipeline, `sanity_pass` and `quality_pass` currently act as "cleansing models" (inline transformations). The analog to dbt convention is moving "blocking" assertions into a separate stage or a `validator.py` that runs against the SQLite/JSON artifact *after* each stage. 
*   **Opinion:** Adopt the **`dbt build` pattern**: run "cleansing" inline (as you do now), but treat "critical assertions" (e.g., `incident_date` cannot be in the future) as a separate validation pass that can fail the run. Separate code from configuration: define validation rules in a schema or YAML, not just hardcoded Python functions.

## 2. Dagster Asset-Checks vs Prefect Validation
**Ecosystem Pattern:** Dagster 1.5+ treats **Asset Checks** as first-class entities that return a `CheckResult` (PASSED/FAILED) with `Severity.WARN` or `Severity.ERROR`. Prefect uses `validation` tasks that can halt the flow.
**Application:** The pipeline should move from "silently fixing" (mutating) to "reporting and flagging." 
*   **Canonical Structure:** 
    *   **Level 0 (Debug):** LLM hallucinations (repaired silently, e.g., script purity).
    *   **Level 1 (Warning):** Conflicts resolved but flagged (e.g., city mismatch).
    *   **Level 2 (Blocking):** Schema violations (e.g., missing mandatory `victim_name`).
*   **Output:** Every stage should emit a `validation_report.json` showing pass/fail counts and specific error IDs, allowing the UI to show a "Data Health" dashboard.

## 3. Audit-log format for downstream consumers
**Ecosystem Pattern:** **W3C PROV** and **OpenLineage** define lineage as a graph of inputs, jobs, and outputs. 
**Application:** The React UI needs to know *why* a case exists. 
*   **Recommendation:** Move beyond a simple `_reconciled: true` flag. Implement a `reconciliation_provenance: list[dict]` field. 
    *   **Format:** `{"action": "merge", "from_run_id": "...", "matching_rule": "jaro_name_match", "confidence_at_merge": 0.92}`.
    *   This follows the **OpenLineage Facets** pattern, where metadata is attached to the dataset as a versioned "facet."

## 4. Pydantic v2 schema evolution for serialized JSON
**Ecosystem Pattern:** **JSON Schema 2020-12** and **AsyncAPI** recommend "forward compatibility" where consumers ignore unknown fields and provide defaults for missing ones.
**Application:** 
*   **Frontend:** The React UI (`ui/frontend/`) must use `Optional` chaining and provide "N/A" or "Unknown" defaults.
*   **Contract:** Use Pydantic's `extra='ignore'` on the consumer side to prevent crashes on new fields. 
*   **Evolution:** When adding fields like `tier_coverage`, version the top-level envelope: `{"schema_version": "2.1", "cases": [...]}`. This allows the UI to choose different rendering logic or warn the user about "legacy data."

## 5. Operator UX for opt-out flags
**Ecosystem Pattern:** **`kubectl`** uses `--dry-run`, **`terraform`** uses `-target`. **`pytest`** uses `-k` for selection. 
**Application:** 
*   **Pattern:** Instead of a sprawl of `--no-X` flags, use a **Stage Exclusion** or **Feature Flag** pattern.
*   **Recommendation:** `--skip <stage_name>` (e.g., `--skip media --skip export`).
*   **Scaling:** For fine-grained control (sanity/quality), use a sub-command style or a config override: `--config "validation.sanity=false"`. This mimics **`git -c`** or **`npm config set`**. It avoids flag-rot in the top-level CLI.

---
**Summary for Implementers:** 
Prioritize the **Audit-log** and **Asset-check** patterns. Downstream UI users trust data more when they can see the "workings" (provenance) and the "health score" (validation report) rather than just the final merged state.
