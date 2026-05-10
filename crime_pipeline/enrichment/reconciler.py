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
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

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


def _merge_pair(strong: dict, weak: dict) -> dict:
    """Fill null fields in `strong` from `weak`; combine sources and flags."""
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


def reconcile_file(
    path: str | Path,
    jaro_threshold: float = 0.85,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Load an output JSON, merge fragmented clusters, optionally write back.

    Returns a summary: {"merged_pairs": [...], "cases_before": n, "cases_after": m}
    """
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        envelope = json.load(f)

    cases: list[dict] = envelope.get("cases") or []
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
        log.info("reconciler_nothing_to_merge", path=str(path))
        return {"merged_pairs": [], "cases_before": n_before, "cases_after": n_before}

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
            canonical = _merge_pair(canonical, cases[weak_idx])
        kept.append(canonical)

    envelope["cases"] = kept
    envelope["case_count"] = len(kept)
    stats = envelope.get("stats") or {}
    stats["reconciled_merges"] = len(merged_pairs)
    envelope["stats"] = stats

    if not dry_run:
        with path.open("w", encoding="utf-8") as f:
            json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)
        log.info("reconciler_written", path=str(path), before=n_before, after=len(kept))

    return {
        "merged_pairs": merged_pairs,
        "cases_before": n_before,
        "cases_after": len(kept),
    }
