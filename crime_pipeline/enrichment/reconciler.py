"""
Post-merge cluster reconciler.

Reads an exported output JSON and merges canonical cases that are likely
the same incident but were fragmented by the dedup blocking step (e.g.
breaking-news articles with no city/date ended up in a different block
than the confirmed-death article).

Merge criteria (ALL must hold):
  - Jaro-Winkler(name_a, name_b) >= jaro_threshold (default 0.85)
  - City does not actively conflict (null is OK on either side)
  - Incident date does not actively conflict at YYYY-MM granularity

Merge strategy:
  - Keep the case with the most sources as the canonical record
  - Fill null fields in the canonical from the weaker case
  - Combine source lists (deduplicated by URL)
  - Combine flags (deduplicated)
  - Re-derive confidence as weighted average of all sources
  - Remove the weaker case from the output

Writes the reconciled JSON back in-place and returns a summary dict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


@dataclass(slots=True)
class ReconcileResult:
    """Pure-data return from `reconcile_cases`. No file I/O involved."""

    cases: list[dict[str, Any]]
    merged_pairs: list[dict[str, Any]]
    cases_before: int
    cases_after: int

    def summary(self) -> dict[str, Any]:
        """Backward-compatible summary dict (matches legacy `reconcile_file` return)."""
        return {
            "merged_pairs": self.merged_pairs,
            "cases_before": self.cases_before,
            "cases_after": self.cases_after,
        }


_SCALAR_FILLABLE = [
    "victim_name", "victim_name_ar", "victim_name_he", "victim_name_en",
    "victim_age", "victim_gender",
    "incident_date", "death_date", "city", "neighborhood", "district",
    "region", "weapon_type", "suspect_status", "legal_status",
    "victim_outcome", "confidence_score",
]


def _jaro(a: str, b: str) -> float:
    from crime_pipeline.dedup.name_normalizer import jaro_winkler_similarity
    return jaro_winkler_similarity(a, b)


def _city_conflicts(a: dict, b: dict) -> bool:
    ca = (a.get("city") or "").strip().lower()
    cb = (b.get("city") or "").strip().lower()
    return bool(ca and cb and ca != cb)


def _date_conflicts(a: dict, b: dict) -> bool:
    da = str(a.get("incident_date") or "")[:7]
    db = str(b.get("incident_date") or "")[:7]
    return bool(da and db and da != db)


def _merge_pair(
    strong: dict,
    weak: dict,
    provenance_entry: dict | None = None,
) -> dict:
    """Fill null fields in `strong` from `weak`; combine sources and flags.

    `provenance_entry`, if given, is appended to ``strong["reconciliation_provenance"]``
    so consumers (the React UI especially) can show "merged from N sources" with
    the rule and jaro_score that drove each merge.
    """
    if provenance_entry is not None:
        strong.setdefault("reconciliation_provenance", []).append(provenance_entry)

    for field in _SCALAR_FILLABLE:
        if strong.get(field) is None and weak.get(field) is not None:
            strong[field] = weak[field]

    # Aliases — additive union
    aliases = list(strong.get("aliases") or [])
    for a in (weak.get("aliases") or []):
        if a and a not in aliases:
            aliases.append(a)
    # Also pull the weak case's primary names into aliases if not already there
    primary_names = {strong.get(k) for k in
                     ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en")
                     if strong.get(k)}
    for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
        v = weak.get(k)
        if v and v not in primary_names and v not in aliases:
            aliases.append(v)
    strong["aliases"] = aliases

    # Sources — deduplicated by URL
    existing_urls = {s.get("url") for s in (strong.get("sources") or [])}
    for s in (weak.get("sources") or []):
        if s.get("url") not in existing_urls:
            (strong.setdefault("sources", [])).append(s)
            existing_urls.add(s.get("url"))

    # Flags — union
    flags = list(strong.get("flags") or [])
    for f in (weak.get("flags") or []):
        if f not in flags:
            flags.append(f)
    # Remove single_source flag if we now have multiple sources
    if len(strong.get("sources") or []) >= 2 and "single_source" in flags:
        flags.remove("single_source")
    strong["flags"] = flags

    # Rebuild confidence as weighted average across all sources
    weights = {"police": 3, "ynet": 2, "kan": 2, "haaretz": 2, "panet": 1, "bokra": 1}
    sources = strong.get("sources") or []
    if sources:
        total_w, total_score = 0, 0.0
        for s in sources:
            pub = s.get("actual_publisher") or s.get("source_name") or ""
            w = weights.get(pub, 1)
            total_w += w
            total_score += w * (s.get("confidence_score") or 0.5)
        strong["confidence_score"] = round(min(1.0, total_score / max(total_w, 1)), 3)

    strong["_reconciled"] = True
    return strong


def reconcile_cases(
    cases: list[dict[str, Any]],
    jaro_threshold: float = 0.85,
) -> ReconcileResult:
    """
    Pure in-memory reconciliation. No file I/O, no module-level state.

    Merges fragmented clusters using the same Jaro-Winkler + city/date gating
    as `reconcile_file`. Each merged canonical case gets a
    ``reconciliation_provenance`` entry recording the rule and jaro_score that
    drove the merge, so downstream consumers (the React UI) can surface the
    audit trail without re-reading a side-file.
    """
    n_before = len(cases)
    merged_pairs: list[dict] = []

    # Build union-find over case indices
    parent = list(range(len(cases)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    def _all_names(case: dict) -> list[str]:
        """All known name strings for a case: primary fields + aliases."""
        names = []
        for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
            v = (case.get(k) or "").strip()
            if v:
                names.append(v)
        for v in (case.get("aliases") or []):
            v = (v or "").strip()
            if v and v not in names:
                names.append(v)
        return names

    def _best_jaro(names_a: list[str], names_b: list[str]) -> float:
        """Best Jaro-Winkler score across all cross-product name pairs."""
        best = 0.0
        for na in names_a:
            for nb in names_b:
                s = _jaro(na, nb)
                if s > best:
                    best = s
        return best

    for i in range(len(cases)):
        for j in range(i + 1, len(cases)):
            a, b = cases[i], cases[j]
            if _city_conflicts(a, b) or _date_conflicts(a, b):
                continue

            names_a = _all_names(a)
            names_b = _all_names(b)

            # Rule 1: both have at least one name → best alias-aware Jaro match
            if names_a and names_b:
                score = _best_jaro(names_a, names_b)
                if score >= jaro_threshold:
                    union(i, j)
                    merged_pairs.append({
                        "i": i, "j": j,
                        "name_a": a.get("victim_name") or names_a[0],
                        "name_b": b.get("victim_name") or names_b[0],
                        "jaro": round(score, 3),
                        "rule": "name_match",
                    })
                    log.info(
                        "reconciler_merge",
                        name_a=names_a[0], name_b=names_b[0], jaro=round(score, 3),
                        city_a=a.get("city"), city_b=b.get("city"),
                    )
                    continue

            # Rule 2: one case has no name at all but shares city + YYYY-MM date
            # with the other — almost certainly the same event, different article quality
            city_a = (a.get("city") or "").strip().lower()
            city_b = (b.get("city") or "").strip().lower()
            date_a = str(a.get("incident_date") or "")[:7]
            date_b = str(b.get("incident_date") or "")[:7]
            neither_has_name = not names_a and not names_b
            one_nameless = (not names_a) != (not names_b)
            if one_nameless and not neither_has_name:
                if city_a and city_b and city_a == city_b and date_a and date_b and date_a == date_b:
                    named = names_a[0] if names_a else names_b[0]
                    union(i, j)
                    merged_pairs.append({
                        "i": i, "j": j,
                        "name_a": named, "name_b": "(unnamed)",
                        "jaro": 0.0,
                        "rule": "city_date_match",
                    })
                    log.info(
                        "reconciler_merge_nameless",
                        name=named, city=city_a, date=date_a,
                    )

    if not merged_pairs:
        return ReconcileResult(
            cases=cases,
            merged_pairs=[],
            cases_before=n_before,
            cases_after=n_before,
        )

    # Build a (i,j) -> pair_meta lookup for provenance attribution. Pairs are
    # stored with i<j in merged_pairs; key the lookup with sorted index tuples.
    pair_meta: dict[tuple[int, int], dict] = {
        (min(p["i"], p["j"]), max(p["i"], p["j"])): p for p in merged_pairs
    }

    def _provenance_for(strong_idx: int, weak_idx: int) -> dict[str, Any]:
        """Build a provenance entry for one weak→strong merge inside a cluster.

        Falls back to a "transitive" reason when the weak case is in the same
        union-find cluster as `strong` but they were not directly compared.
        """
        weak_first_url: Any = None
        weak_sources = cases[weak_idx].get("sources") or []
        if weak_sources:
            weak_first_url = weak_sources[0].get("url")
        meta = pair_meta.get((min(strong_idx, weak_idx), max(strong_idx, weak_idx)))
        if meta is not None:
            return {
                "merged_from_url": weak_first_url,
                "reason": meta["rule"],
                "jaro_score": meta["jaro"],
            }
        return {
            "merged_from_url": weak_first_url,
            "reason": "transitive",
            "jaro_score": 0.0,
        }

    # Group by cluster root
    from collections import defaultdict
    clusters: dict[int, list[int]] = defaultdict(list)
    for idx in range(len(cases)):
        clusters[find(idx)].append(idx)

    kept: list[dict] = []
    for root, members in clusters.items():
        if len(members) == 1:
            kept.append(cases[members[0]])
            continue
        # Pick strongest (most sources, then highest confidence)
        members_sorted = sorted(
            members,
            key=lambda idx: (
                -len(cases[idx].get("sources") or []),
                -(cases[idx].get("confidence_score") or 0),
            ),
        )
        canonical_idx = members_sorted[0]
        canonical = dict(cases[canonical_idx])
        for weak_idx in members_sorted[1:]:
            canonical = _merge_pair(
                canonical,
                cases[weak_idx],
                provenance_entry=_provenance_for(canonical_idx, weak_idx),
            )
        kept.append(canonical)

    return ReconcileResult(
        cases=kept,
        merged_pairs=merged_pairs,
        cases_before=n_before,
        cases_after=len(kept),
    )


def reconcile_file(
    path: str | Path,
    jaro_threshold: float = 0.85,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Backward-compatible CLI wrapper around `reconcile_cases`.

    Loads an output JSON, calls the pure in-memory reconciler, optionally
    writes the result back, and returns the legacy summary dict shape so
    existing callers (the `--reconcile <json>` CLI mode) keep working.
    """
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        envelope = json.load(f)

    result = reconcile_cases(envelope.get("cases") or [], jaro_threshold=jaro_threshold)

    if not result.merged_pairs:
        log.info("reconciler_nothing_to_merge", path=str(path))
        return result.summary()

    envelope["cases"] = result.cases
    envelope["case_count"] = result.cases_after
    stats = envelope.get("stats") or {}
    stats["reconciled_merges"] = len(result.merged_pairs)
    envelope["stats"] = stats

    if not dry_run:
        with path.open("w", encoding="utf-8") as f:
            json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)
        log.info(
            "reconciler_written",
            path=str(path),
            before=result.cases_before,
            after=result.cases_after,
        )

    return result.summary()
