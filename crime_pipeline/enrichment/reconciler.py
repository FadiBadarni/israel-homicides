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
    "confidence_score",
]

_VICTIM_OUTCOME_ORDER = {
    "survived": 0,
    "critical": 1,
    "unknown": 2,
    "died": 3,
}


def _jaro(a: str, b: str) -> float:
    from crime_pipeline.dedup.name_normalizer import jaro_winkler_similarity
    return jaro_winkler_similarity(a, b)


def _city_conflicts(a: dict, b: dict) -> bool:
    """True if both cases have a city AND those cities clearly differ.

    Uses the gazetteer to canonicalize before comparing — so the bare
    'عرابة' and the official long form 'عرابة البطوف' are treated as
    the same city instead of as a hard conflict. Falls back to literal
    string compare when the gazetteer doesn't know the city.

    Pre-fix bug: the live --cities Arab48 backfill produced two records
    for Bakr Yassin (one with city='عرابة', the other with city='عرابة
    البطوف') that the reconciler refused to merge because this function
    flagged a conflict before the Jaro name check could run.
    """
    ca = (a.get("city") or "").strip()
    cb = (b.get("city") or "").strip()
    if not (ca and cb):
        return False
    if ca.lower() == cb.lower():
        return False

    # Both sides have a city and they differ literally — check gazetteer
    # for an alias-aware match before declaring a real conflict.
    from crime_pipeline.utils.gazetteer import normalize_city
    ra = normalize_city(ca)
    rb = normalize_city(cb)
    if ra and rb:
        return ra.get("name_en") != rb.get("name_en")

    # One side unknown to gazetteer → fall back to literal compare
    return True


def _date_conflicts(a: dict, b: dict) -> bool:
    da = str(a.get("incident_date") or "")[:7]
    db = str(b.get("incident_date") or "")[:7]
    return bool(da and db and da != db)


def _resolve_victim_outcome(strong: dict, weak: dict) -> tuple[str | None, str | None]:
    """
    Resolve victim_outcome with fatal-first semantics.

    Reconciliation can merge a breaking-news "survived/critical" case into a
    later confirmed-death case, or vice versa. A confirmed death must win so a
    homicide is not dropped by the export-time non-fatal filter.
    """
    outcomes = [
        o for o in (strong.get("victim_outcome"), weak.get("victim_outcome"))
        if o is not None
    ]
    if not outcomes:
        return None, None

    distinct = set(outcomes)
    if "died" in distinct:
        flag = "outcome_conflict" if len(distinct) > 1 else None
        return "died", flag

    chosen = max(outcomes, key=lambda o: _VICTIM_OUTCOME_ORDER.get(o, -1))
    flag = "outcome_conflict" if len(distinct) > 1 else None
    return chosen, flag


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

    victim_outcome, outcome_flag = _resolve_victim_outcome(strong, weak)
    if victim_outcome is not None:
        strong["victim_outcome"] = victim_outcome

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

    # Media — union deduped by media_id (phash hash), so absorbed cases
    # don't lose their portraits/evidence when their parent case wins the
    # reconcile. Falls back to primary_url when media_id is absent.
    # Without this union, a Bakr-shape case that grew via 3 merges would
    # end up with the strong-case's empty media list, dropping 30+
    # harvested images on the floor.
    for field in ("media", "media_evidence"):
        existing_ids = {
            (m.get("media_id") or m.get("primary_url"))
            for m in (strong.get(field) or [])
        }
        for m in (weak.get(field) or []):
            key = m.get("media_id") or m.get("primary_url")
            if key and key not in existing_ids:
                (strong.setdefault(field, [])).append(m)
                existing_ids.add(key)

    # Flags — union
    flags = list(strong.get("flags") or [])
    for f in (weak.get("flags") or []):
        if f not in flags:
            flags.append(f)
    if outcome_flag and outcome_flag not in flags:
        flags.append(outcome_flag)
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

    def _name_tokens(name: str) -> list[str]:
        """Romanized token list, dropping 1-char tokens (initials/diacritics)."""
        from crime_pipeline.dedup.name_normalizer import romanize_name
        return [t for t in romanize_name(name).split() if len(t) > 1]

    def _token_containment_match(
        names_a: list[str], names_b: list[str]
    ) -> bool:
        """True when one name is the other with extra middle tokens inserted.

        Required guards (preventing over-merging on common first names):
        - Shorter side must have ≥2 tokens (single-token names too risky)
        - Every short token must have a per-token Jaro ≥ 0.85 partner
          in the long side. Fuzzy by design — Hebrew "חוסיין" romanizes
          to ``hwsyyn`` and Arabic "حسين" to ``hsyn`` (the Hebrew
          transliteration writes long-/i:/ as ``יי`` plus a ``ו`` vowel
          marker that Arabic spelling drops). Bare ``set(short) < set(long_)``
          rejects these as different tokens; per-token Jaro recognises them.
        - First AND last tokens must align positionally (also fuzzy) so
          "X Y" matches "X Z Y" but not "X Y" vs "Y X" reorderings.

        Catches the inserted-middle-name pattern that Jaro can fall below
        threshold on for longer middle names. Mirrors verify's
        ``_verify_names_match`` fuzzy logic so reconcile and verify make
        the same call on the same input.
        """
        TOKEN_JARO_THRESHOLD = 0.85
        for na in names_a:
            ta = _name_tokens(na)
            for nb in names_b:
                tb = _name_tokens(nb)
                if len(ta) < 2 or len(tb) < 2 or len(ta) == len(tb):
                    continue
                short, long_ = (ta, tb) if len(ta) < len(tb) else (tb, ta)
                # Every short-side token has a Jaro≥0.85 partner in long.
                all_have_partner = all(
                    any(
                        _jaro(st, lt) >= TOKEN_JARO_THRESHOLD
                        for lt in long_
                    )
                    for st in short
                )
                if not all_have_partner:
                    continue
                # Positional anchor: first AND last tokens must align.
                first_ok = _jaro(short[0], long_[0]) >= TOKEN_JARO_THRESHOLD
                last_ok = _jaro(short[-1], long_[-1]) >= TOKEN_JARO_THRESHOLD
                if first_ok and last_ok:
                    return True
        return False

    def _source_urls(case: dict) -> set[str]:
        """All source URLs cited by a case. Used to enforce intra-article
        exclusion at reconcile time: two cases that share a source URL
        are different victims of a multi-victim article (the only way one
        URL can appear in two cases is via the multi-victim explode), and
        must not be re-merged here. Without this guard, the
        cross-article cluster of N sibling virtual records re-collapses
        the very victims the explode step worked to separate."""
        urls = set()
        for s in (case.get("sources") or []):
            u = s.get("url")
            if u:
                urls.add(u)
        return urls

    _STRONG_NAME_JARO = 0.95

    def _multi_token_names(names: list[str]) -> list[str]:
        """Filter to names with ≥2 tokens after romanization+stripping.
        Single-token given names (Mohammed, Ahmed, Ali) are too common
        to use as the basis for bypassing the city-conflict veto — they
        match across genuinely different victims and create mega-cluster
        cascades via the union-find merge."""
        out: list[str] = []
        for n in names:
            if len(_name_tokens(n)) >= 2:
                out.append(n)
        return out

    for i in range(len(cases)):
        for j in range(i + 1, len(cases)):
            a, b = cases[i], cases[j]
            if _date_conflicts(a, b):
                continue

            names_a = _all_names(a)
            names_b = _all_names(b)

            # City conflict is normally a hard veto. But the same victim
            # is often described via different geographic angles across
            # publishers — residence vs incident-site vs funeral-city vs
            # hospital-city. Bypass the city veto when the cross-script
            # romanized name match is very strong (Jaro ≥ 0.95) or the
            # token-containment match fires; those signals alone are
            # already strong enough that nearby-city ambiguity is far
            # more likely than a same-name different-victim collision.
            #
            # CRITICAL: the bypass uses only MULTI-TOKEN names. Single-
            # token given names like "Mohammed" Jaro at 1.0 across
            # unrelated victims and cascade via transitive union-find,
            # producing 100+ source mega-clusters of unrelated incidents.
            # (See debate at 2025-gap-smart-cheap-001/ + the 225-source
            # Mohammed Almalachi false-merge that this fix breaks up.)
            if _city_conflicts(a, b):
                strong_name_match = False
                mt_names_a = _multi_token_names(names_a)
                mt_names_b = _multi_token_names(names_b)
                if mt_names_a and mt_names_b:
                    strong_name_match = (
                        _best_jaro(mt_names_a, mt_names_b) >= _STRONG_NAME_JARO
                        or _token_containment_match(mt_names_a, mt_names_b)
                    )
                if not strong_name_match:
                    continue

            # Intra-article exclusion (mirrors the dedup stage's rule
            # for multi-victim records). Two cases sharing any source
            # URL AND with different names describe distinct victims of
            # one multi-victim article — merging them re-collapses the
            # very victims the explode step worked to separate.
            #
            # The "AND different names" qualifier is critical: when
            # cross-run aggregation re-discovers the same article in
            # multiple keyword sweeps, the same victim shows up in N
            # per-run JSONs all citing the same URL. Those legitimate
            # duplicates have NAME MATCH and MUST merge. The Feb 2026
            # Hadi Nassar triple murder surfaced this gap — 3 duplicate
            # rows in validated_2026_ytd.json.
            shared_urls = _source_urls(a) & _source_urls(b)
            if shared_urls and names_a and names_b:
                name_overlap = (
                    _best_jaro(names_a, names_b) >= jaro_threshold
                    or _token_containment_match(names_a, names_b)
                )
                if not name_overlap:
                    continue
            elif shared_urls:
                # Shared URL but at least one side has no name — leave
                # as-is (the name-less case will likely be handled by
                # Rule 2 below if city+date align).
                continue

            # Rule 1: both have at least one name → best alias-aware Jaro match
            # OR token-containment match (catches inserted-middle-name cases
            # where Jaro can fall below threshold for longer middle names).
            if names_a and names_b:
                score = _best_jaro(names_a, names_b)
                containment = _token_containment_match(names_a, names_b)
                if score >= jaro_threshold or containment:
                    union(i, j)
                    rule = (
                        "name_match"
                        if score >= jaro_threshold
                        else "name_token_containment"
                    )
                    merged_pairs.append({
                        "i": i, "j": j,
                        "name_a": a.get("victim_name") or names_a[0],
                        "name_b": b.get("victim_name") or names_b[0],
                        "jaro": round(score, 3),
                        "rule": rule,
                    })
                    log.info(
                        "reconciler_merge",
                        name_a=names_a[0], name_b=names_b[0], jaro=round(score, 3),
                        rule=rule,
                        city_a=a.get("city"), city_b=b.get("city"),
                    )
                    continue

            # Rule 2: one case has no name at all but shares city + exact
            # incident date with the other. Month-level matching is too broad
            # for default runs because one city can have multiple incidents.
            city_a = (a.get("city") or "").strip().lower()
            city_b = (b.get("city") or "").strip().lower()
            date_a = str(a.get("incident_date") or "")[:10]
            date_b = str(b.get("incident_date") or "")[:10]
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
