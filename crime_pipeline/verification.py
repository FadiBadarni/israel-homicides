"""Truth-vs-pipeline verification (Strategy C stage 3).

Loads a JSONL ground-truth file and a pipeline output JSON, matches each
truth record to the closest pipeline case via the existing
``is_same_incident()`` gate (Jaro-Winkler ≥ 0.70 + city + ±5-day date
window), and reports precision / recall / F1 plus the false-negative
and false-positive sets.

Truth file format (one JSON object per line)::

    {"city": "Arraba", "victim_name_he": "בכר מחמוד יאסין",
     "victim_name_ar": "بكر ياسين", "incident_date": "2026-01-03"}

Any subset of fields is acceptable — the matcher uses whatever's there.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class VerifyResult:
    """Pure-data return from ``verify_run_against_truth``."""

    truth_count: int
    pipeline_count: int
    true_positive: int
    false_negative: int
    false_positive: int
    missing_truth: list[dict[str, Any]]   # cases in truth but not in pipeline
    extra_pipeline: list[dict[str, Any]]  # cases in pipeline but not in truth

    @property
    def precision(self) -> float:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return (2 * p * r / (p + r)) if (p + r) else 0.0

    def summary_dict(self) -> dict[str, Any]:
        return {
            "truth_count": self.truth_count,
            "pipeline_count": self.pipeline_count,
            "true_positive": self.true_positive,
            "false_negative": self.false_negative,
            "false_positive": self.false_positive,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "missing_truth": self.missing_truth,
            "extra_pipeline": self.extra_pipeline,
        }


def load_truth_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read JSONL truth file. Skips blank lines and ``#`` comments."""
    records: list[dict[str, Any]] = []
    p = Path(path)
    for line_no, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}:{line_no} invalid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError(f"{p}:{line_no} expected JSON object, got {type(obj)}")
        records.append(obj)
    return records


def load_pipeline_cases(path: str | Path) -> list[dict[str, Any]]:
    """Read a pipeline output JSON envelope and return its cases list."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = envelope.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError(f"{path}: expected envelope['cases'] to be a list")
    return cases


def _truth_to_case_shape(truth: dict[str, Any]) -> dict[str, Any]:
    """Convert a truth record into the dict shape ``is_same_incident``
    expects on its first arg (named like a CanonicalCaseSchema dump)."""
    return {
        "victim_name": truth.get("victim_name") or truth.get("victim_name_he")
                       or truth.get("victim_name_ar") or truth.get("victim_name_en"),
        "victim_name_ar": truth.get("victim_name_ar"),
        "victim_name_he": truth.get("victim_name_he"),
        "victim_name_en": truth.get("victim_name_en"),
        "city": truth.get("city"),
        "city_normalized": truth.get("city_normalized") or {},
        "incident_date": truth.get("incident_date"),
        "aliases": truth.get("aliases") or [],
    }


_VERIFY_NAME_JARO_HIGH = 0.95        # full-string Jaro at/above this → auto-accept
_VERIFY_NAME_JARO_LOW = 0.85         # below this → auto-reject
_VERIFY_TOKEN_JARO_THRESHOLD = 0.85  # per-token match threshold for overlap count
_VERIFY_REQUIRED_TOKEN_OVERLAP = 2   # ≥ this many shared tokens needed in ambiguous zone
_VERIFY_DATE_WINDOW_DAYS = 10


def _verify_names_match(truth_names: list[str], case_names: list[str]) -> bool:
    """Strict name-match for verify, with token-overlap fallback for the
    ambiguous Jaro zone and a containment escape hatch for middle-name
    insertions.

    Why this is more complex than a single threshold: a flat Jaro ≥ 0.85
    accepts the Bakr↔Bakr-Mahmoud substring case (legit) but ALSO accepts
    family-name collisions like 'Ahmed Nassar' ↔ 'Nathim Nassar' (Jaro
    ≈ 0.878). The first shares 2 tokens (bakr + yassin), the second
    shares only 1 (nassar). Token-overlap discriminates the two cases.

    A separate failure mode: 'وفاء بدران حصارمة' vs 'وفاء محمود بدران حصارمة'
    has full-string Jaro ≈ 0.837 (extra middle name drags it under 0.85),
    yet every truth token has a perfect partner. That's containment,
    not a collision — handled by the escape hatch below.

    Decision logic:
      • Jaro ≥ 0.95 → ACCEPT (essentially identical)
      • Token-containment: ≥ 2 tokens on the short side AND every short
        token has a partner at per-token Jaro ≥ 0.85 → ACCEPT (overrides
        the < 0.85 reject; handles middle-name insertions and Arabic
        article elisions)
      • Jaro < 0.85 → REJECT
      • Jaro ∈ [0.85, 0.95) → require ≥ 2 shared tokens. One shared
        token is the family-name collision pattern.
    """
    from crime_pipeline.dedup.name_normalizer import (
        jaro_winkler_similarity, romanize_name,
    )
    truth_rom = [romanize_name(n) for n in truth_names if n]
    case_rom = [romanize_name(n) for n in case_names if n]
    truth_rom = [n for n in truth_rom if n]
    case_rom = [n for n in case_rom if n]
    if not truth_rom or not case_rom:
        return False

    best = 0.0
    best_pair: tuple[str, str] | None = None
    for tn in truth_rom:
        for cn in case_rom:
            s = jaro_winkler_similarity(tn, cn)
            if s > best:
                best = s
                best_pair = (tn, cn)

    if best >= _VERIFY_NAME_JARO_HIGH:
        return True

    if best_pair is None:
        return False
    tn, cn = best_pair
    t_tokens = [t for t in tn.split() if len(t) > 1]
    c_tokens = [t for t in cn.split() if len(t) > 1]
    if not t_tokens or not c_tokens:
        return False

    short, long_ = (
        (t_tokens, c_tokens) if len(t_tokens) <= len(c_tokens)
        else (c_tokens, t_tokens)
    )
    shared = 0
    for st in short:
        for lt in long_:
            if jaro_winkler_similarity(st, lt) >= _VERIFY_TOKEN_JARO_THRESHOLD:
                shared += 1
                break

    # When the short side is STRICTLY shorter than the long side AND
    # every short token has a partner in the long side, this is a
    # "subset" case — short is potentially a substring of long. The
    # subset case has TWO failure modes the bare overlap-count rule
    # can't tell apart:
    #
    #   (a) Legit subset — same person, different name granularity:
    #         بكر ياسين            ⊂ بكر محمود ياسين      (Bakr middle-name)
    #         وفاء بدران حصارمة   ⊂ وفاء محمود بدران حصارمة (Wafa middle-name)
    #         Both have first AND last tokens aligned positionally.
    #
    #   (b) Father↔son collision (Arab naming pattern: son named after
    #       grandfather — son's full name is a token-subset of father's):
    #         نظيم نصار (son, 15yo)  ⊂  أدهم نظيم نصار (dad, 34yo)
    #         The son's first token "Nadhim" does NOT match the dad's
    #         first token "Adham". The positional anchor rejects this.
    #
    # Anchor on first AND last token positionally to discriminate. This
    # mirrors reconciler._token_containment_match.
    is_strict_subset = len(short) < len(long_) and shared == len(short)
    if is_strict_subset:
        first_match = (
            jaro_winkler_similarity(short[0], long_[0])
            >= _VERIFY_TOKEN_JARO_THRESHOLD
        )
        last_match = (
            jaro_winkler_similarity(short[-1], long_[-1])
            >= _VERIFY_TOKEN_JARO_THRESHOLD
        )
        # Subset with positional anchor → accept (overrides the < 0.85
        # full-string reject for the middle-name-insertion case).
        if len(short) >= 2 and first_match and last_match:
            return True
        # Subset WITHOUT positional anchor → reject regardless of how
        # many tokens match. Without this, the ambiguous-zone fallback
        # below would still accept father↔son via shared >= 2.
        return False

    if best < _VERIFY_NAME_JARO_LOW:
        return False

    return shared >= _VERIFY_REQUIRED_TOKEN_OVERLAP


def _verify_dates_match(truth_date: Any, case_date: Any) -> bool:
    """±10-day window. Loosened from is_same_incident's ±5 because truth
    dates are often inferred (e.g. from a sentencing-article publication
    date rather than the actual incident)."""
    from datetime import date as _date, timedelta
    def _coerce(d: Any) -> _date | None:
        if d is None:
            return None
        if isinstance(d, _date):
            return d
        try:
            return _date.fromisoformat(str(d))
        except (ValueError, TypeError):
            return None
    a, b = _coerce(truth_date), _coerce(case_date)
    if a is None or b is None:
        return False
    return abs((a - b).days) <= _VERIFY_DATE_WINDOW_DAYS


def _verify_cities_match(truth_city: str | None, case: dict[str, Any]) -> bool:
    """Gazetteer-aware city match — both must resolve to the same canonical
    record. Returns False if either side lacks a city."""
    if not truth_city:
        return False
    case_city = case.get("city")
    if not case_city:
        return False
    if truth_city.strip().lower() == case_city.strip().lower():
        return True
    from crime_pipeline.utils.gazetteer import normalize_city
    a = normalize_city(truth_city)
    b = normalize_city(case_city)
    if a and b:
        return a.get("name_en") == b.get("name_en")
    return False


def _verify_match(truth: dict[str, Any], case: dict[str, Any]) -> bool:
    """Strict per-record matcher for verify.

    Required: AT LEAST ONE positive signal must hold —
      (a) name match (Jaro ≥ 0.85 on romanized form), OR
      (b) city + date match (gazetteer city + ±10 days)

    Null fields are NEVER treated as auto-pass: a truth record with
    only a name and no city/date is matched ONLY by name. A pipeline
    case with no name can only be matched if truth supplies city + date.
    This is stricter than ``is_same_incident``'s skip-on-null behavior,
    which is correct for intra-pipeline merging but produces spurious
    verify hits when truth records lack context.
    """
    truth_names = [
        truth.get("victim_name"),
        truth.get("victim_name_ar"),
        truth.get("victim_name_he"),
        truth.get("victim_name_en"),
    ]
    case_names = [
        case.get("victim_name"),
        case.get("victim_name_ar"),
        case.get("victim_name_he"),
        case.get("victim_name_en"),
    ]
    case_names += list(case.get("aliases") or [])

    name_match = _verify_names_match(
        [n for n in truth_names if n], [n for n in case_names if n]
    )

    city_match = _verify_cities_match(truth.get("city"), case)
    date_match = _verify_dates_match(
        truth.get("incident_date") or truth.get("death_date"),
        case.get("incident_date") or case.get("death_date"),
    )

    # Strict: name match alone is enough; city alone or date alone is NOT.
    # City + date together is enough (covers anonymous victims).
    return name_match or (city_match and date_match)


def verify_run_against_truth(
    truth_records: list[dict[str, Any]],
    pipeline_cases: list[dict[str, Any]],
) -> VerifyResult:
    """Match truth records to pipeline cases.

    Uses a strict per-record matcher (``_verify_match``) — different from
    the intra-pipeline ``is_same_incident`` gate because verify lacks
    surrounding article context and can't tolerate null-field auto-passes.
    Greedy 1:1 matching — once a pipeline case is matched it can't match
    another truth record.
    """
    matched_truth_idx: set[int] = set()
    matched_case_idx: set[int] = set()

    for ti, truth in enumerate(truth_records):
        for ci, case in enumerate(pipeline_cases):
            if ci in matched_case_idx:
                continue
            try:
                ok = _verify_match(truth, case)
            except Exception:
                continue
            if ok:
                matched_truth_idx.add(ti)
                matched_case_idx.add(ci)
                break

    tp = len(matched_case_idx)
    fn = len(truth_records) - len(matched_truth_idx)
    fp = len(pipeline_cases) - len(matched_case_idx)

    missing_truth = [
        t for i, t in enumerate(truth_records) if i not in matched_truth_idx
    ]
    extra_pipeline = [
        # Don't dump full case JSON — just a fingerprint
        {
            "victim_name": c.get("victim_name"),
            "victim_name_ar": c.get("victim_name_ar"),
            "city": c.get("city"),
            "incident_date": c.get("incident_date"),
            "outcome": c.get("victim_outcome"),
            "confidence_score": c.get("confidence_score"),
            "flags": c.get("flags"),
        }
        for i, c in enumerate(pipeline_cases) if i not in matched_case_idx
    ]

    return VerifyResult(
        truth_count=len(truth_records),
        pipeline_count=len(pipeline_cases),
        true_positive=tp,
        false_negative=fn,
        false_positive=fp,
        missing_truth=missing_truth,
        extra_pipeline=extra_pipeline,
    )
