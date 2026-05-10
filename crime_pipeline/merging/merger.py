"""
Merges a cluster of article extractions into one canonical CanonicalCaseSchema record.

A "cluster" is a group of articles (from one or more sources) determined by upstream
deduplication to describe the same homicide incident. The merger applies field-level
resolution rules and records all disagreements as non-fatal flags + per-source values
in the conflicts dict.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import structlog

from crime_pipeline.merging.conflict_resolver import (
    resolve_age,
    resolve_boolean_or,
    resolve_by_priority,
    resolve_count,
    resolve_status,
)
from crime_pipeline.models import (
    CanonicalCaseSchema,
    ExtractedArticleData,
    SourceRef,
)
from crime_pipeline.utils.gazetteer import normalize_city

log = structlog.get_logger()


def get_priority_weight(source: str) -> int:
    """Return source priority for confidence weighting (0=police, 1=ynet, 2=panet)."""
    return {"police": 0, "ynet": 1, "panet": 2}.get(source, 3)


class CaseMerger:
    """Merges multiple ExtractedArticleData records (with metadata) into one canonical case."""

    def merge_cluster(
        self,
        cluster: list[dict],
        pipeline_run_id: str = "",
    ) -> CanonicalCaseSchema:
        """
        Merge a cluster of extractions about the same incident into one canonical record.

        Args:
            cluster: list of dicts, each with keys:
                - "extraction": ExtractedArticleData
                - "url": str
                - "source": str (one of "police", "ynet", "panet")
                - "language": str ("ar" or "he")
                - "published_at": datetime | None
            pipeline_run_id: Identifier for the pipeline run producing this case.

        Returns:
            A fully populated CanonicalCaseSchema with merged fields, source refs,
            conflict map, flag list, weighted confidence, and review status.

        Raises:
            ValueError: If the cluster is empty.
        """
        if not cluster:
            raise ValueError("Cannot merge empty cluster")

        flags: list[str] = []
        conflicts: dict[str, Any] = {}

        def field_values(field: str) -> list[tuple[Any, str, float]]:
            """Collect (value, source, confidence) tuples for a field across the cluster."""
            return [
                (
                    getattr(m["extraction"], field, None),
                    m["source"],
                    m["extraction"].confidence_score,
                )
                for m in cluster
            ]

        def collect_conflict(field: str, vals: list[tuple[Any, str, float]]) -> None:
            """Record per-source values whenever non-null sources disagree on a field."""
            non_null = [(v, s) for v, s, _ in vals if v is not None]
            distinct_values = {repr(v) for v, _ in non_null}
            if len(distinct_values) > 1:
                # Last write wins per source (a source contributing twice is unusual)
                conflicts[field] = {s: v for v, s in non_null}

        # ---- Victim identity ----

        name_vals = field_values("victim_name")
        victim_name, name_flag = resolve_by_priority(name_vals)
        if name_flag:
            flags.append(f"victim_name:{name_flag}")
            collect_conflict("victim_name", name_vals)

        age_vals = field_values("victim_age")
        victim_age, age_flag = resolve_age(age_vals)
        if age_flag:
            flags.append(f"victim_age:{age_flag}")
            collect_conflict("victim_age", age_vals)

        gender_vals = field_values("victim_gender")
        victim_gender, gender_flag = resolve_by_priority(gender_vals)
        if gender_flag:
            flags.append(f"victim_gender:{gender_flag}")
            collect_conflict("victim_gender", gender_vals)

        # ---- Incident timing ----

        date_vals = field_values("incident_date")
        incident_date, date_flag = resolve_by_priority(date_vals)
        if date_flag:
            flags.append(f"incident_date:{date_flag}")
            collect_conflict("incident_date", date_vals)

        time_vals = field_values("incident_time")
        incident_time, time_flag = resolve_by_priority(time_vals)
        if time_flag:
            flags.append(f"incident_time:{time_flag}")
            collect_conflict("incident_time", time_vals)

        # ---- Location ----

        city_vals = field_values("city")
        city, city_flag = resolve_by_priority(city_vals)
        if city_flag:
            flags.append(f"city:{city_flag}")
            collect_conflict("city", city_vals)

        city_normalized: Optional[dict[str, str]] = None
        if city:
            normalized = normalize_city(city)
            if normalized is None:
                flags.append("city:unknown_locality")
            else:
                # Coerce to plain dict[str, str] in case the gazetteer returns a TypedDict
                city_normalized = {k: v for k, v in dict(normalized).items() if v is not None}

        district_vals = field_values("district")
        district, district_flag = resolve_by_priority(district_vals)
        if district_flag:
            flags.append(f"district:{district_flag}")
        if not district and city_normalized:
            district = city_normalized.get("district")

        # ---- Weapon & victim count ----

        weapon_vals = field_values("weapon_type")
        weapon_type, weapon_flag = resolve_by_priority(weapon_vals)
        if weapon_flag:
            flags.append(f"weapon_type:{weapon_flag}")
            collect_conflict("weapon_type", weapon_vals)

        count_vals = field_values("num_victims")
        num_victims, count_flag = resolve_count(count_vals)
        if count_flag:
            flags.append(f"num_victims:{count_flag}")
            collect_conflict("num_victims", count_vals)

        # ---- Suspect ----

        suspect_name_vals = field_values("suspect_name")
        suspect_name, suspect_name_flag = resolve_by_priority(suspect_name_vals)
        if suspect_name_flag:
            flags.append(f"suspect_name:{suspect_name_flag}")
            collect_conflict("suspect_name", suspect_name_vals)

        status_vals = field_values("suspect_status")
        suspect_status, status_flag = resolve_status(status_vals)
        if status_flag:
            flags.append(f"suspect_status:{status_flag}")
            collect_conflict("suspect_status", status_vals)

        # ---- Context ----

        motive_vals = field_values("motive")
        motive, _ = resolve_by_priority(motive_vals)

        organized_crime, _ = resolve_boolean_or(field_values("organized_crime"))
        family_dispute, _ = resolve_boolean_or(field_values("family_dispute"))

        # ---- Source refs ----

        sources = [
            SourceRef(
                url=m["url"],
                discovery_source=m["source"],
                source_name=m["source"],
                language=m["language"],
                published_at=m.get("published_at"),
                confidence_score=m["extraction"].confidence_score,
            )
            for m in cluster
        ]

        # ---- Confidence aggregation ----
        # Weight inversely by source priority: police=3, ynet=2, panet=1, other=0.
        # If all weights resolve to 0 (unknown sources only), fall back to plain mean.
        confidences = [m["extraction"].confidence_score for m in cluster]
        weights = [3 - get_priority_weight(m["source"]) for m in cluster]
        total_weight = sum(weights)
        if total_weight > 0:
            weighted_sum = sum(c * w for c, w in zip(confidences, weights))
            merged_confidence = weighted_sum / total_weight
        else:
            merged_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        merged_confidence = max(0.0, min(1.0, merged_confidence))

        # Single-source corroboration cap
        if len(cluster) == 1:
            merged_confidence = min(merged_confidence, 0.60)
            flags.append("single_source")

        # ---- Review routing ----
        has_blocking_flag = any(("conflict" in f or "regression" in f) for f in flags)
        review_status = (
            "flagged_for_review"
            if (merged_confidence < 0.55 or has_blocking_flag)
            else "auto"
        )

        case = CanonicalCaseSchema(
            victim_name=victim_name,
            victim_age=victim_age,
            victim_gender=victim_gender,
            incident_date=incident_date,
            incident_time=incident_time,
            city=city,
            city_normalized=city_normalized,
            district=district,
            weapon_type=weapon_type,
            num_victims=num_victims,
            suspect_name=suspect_name,
            suspect_status=suspect_status,
            motive=motive,
            organized_crime=organized_crime,
            family_dispute=family_dispute,
            sources=sources,
            conflicts=conflicts,
            flags=flags,
            confidence_score=round(merged_confidence, 3),
            review_status=review_status,
            pipeline_run_id=pipeline_run_id,
        )

        log.info(
            "cluster_merged",
            cluster_size=len(cluster),
            sources=[m["source"] for m in cluster],
            confidence=case.confidence_score,
            review_status=review_status,
            flag_count=len(flags),
            conflict_fields=list(conflicts.keys()),
            pipeline_run_id=pipeline_run_id,
        )

        return case
