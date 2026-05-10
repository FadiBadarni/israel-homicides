"""Within-article + cross-source media dedup.

Strategy:
    1. Within-article: drop URL-equal duplicates first (already done by harvester).
       Then group by sha256 → keep best (highest resolution caption present).
    2. Cross-source: group by phash Hamming distance ≤ threshold (default 8).
       Optionally collapse near-matches with CLIP cosine ≥ 0.92 (when CLIP
       embeddings are present). Each cluster collapses to one CanonicalMedia.
"""
from __future__ import annotations

import hashlib
from typing import Optional

import structlog

from crime_pipeline.media.hashing import cosine_similarity, hamming_distance
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings

log = structlog.get_logger()


def dedup_within_article(
    candidates: list[MediaCandidate], settings: MediaSettings
) -> list[MediaCandidate]:
    """Collapse exact-byte duplicates (sha256-equal) within a single article."""
    seen_sha: dict[str, MediaCandidate] = {}
    deduped: list[MediaCandidate] = []
    for cand in candidates:
        if cand.sha256 and cand.sha256 in seen_sha:
            existing = seen_sha[cand.sha256]
            # Prefer the candidate with a richer caption
            if (cand.figcaption or cand.caption) and not (existing.figcaption or existing.caption):
                # Promote the new one
                idx = deduped.index(existing)
                deduped[idx] = cand
                seen_sha[cand.sha256] = cand
            continue
        if cand.sha256:
            seen_sha[cand.sha256] = cand
        deduped.append(cand)
    return deduped


def cluster_across_sources(
    candidates: list[MediaCandidate], settings: MediaSettings
) -> list[list[MediaCandidate]]:
    """Group candidates that depict the same image. Returns clusters of indices."""
    n = len(candidates)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    th = settings.phash_distance_threshold
    cos_th = settings.clip_cosine_threshold

    for i in range(n):
        for j in range(i + 1, n):
            ci, cj = candidates[i], candidates[j]
            # Tier 1: exact sha256 match
            if ci.sha256 and ci.sha256 == cj.sha256:
                union(i, j)
                continue
            # Tier 2: pHash within threshold
            if ci.phash and cj.phash:
                if hamming_distance(ci.phash, cj.phash) <= th:
                    union(i, j)
                    continue
            # Tier 3: CLIP cosine ≥ threshold (cross-crop)
            if ci.clip_embedding and cj.clip_embedding:
                if cosine_similarity(ci.clip_embedding, cj.clip_embedding) >= cos_th:
                    union(i, j)
                    continue

    # Build clusters
    by_root: dict[int, list[MediaCandidate]] = {}
    for i, cand in enumerate(candidates):
        by_root.setdefault(find(i), []).append(cand)
    return list(by_root.values())


def select_canonical(cluster: list[MediaCandidate]) -> MediaCandidate:
    """Pick the best representative from a dedup cluster.

    Preference: largest dimensions → richer caption → first encountered.
    """
    def score(c: MediaCandidate) -> tuple:
        area = (c.width or 0) * (c.height or 0)
        cap_len = len(c.figcaption or c.caption or "")
        return (area, cap_len, -ord((c.source_url or "_")[0]))

    return max(cluster, key=score)


def media_id_for(cluster: list[MediaCandidate]) -> str:
    """Stable id for a cluster — phash if available else sha256-of-canonical-url."""
    canonical = select_canonical(cluster)
    if canonical.phash:
        return f"phash:{canonical.phash}"
    if canonical.sha256:
        return f"sha:{canonical.sha256[:16]}"
    return f"url:{hashlib.sha256((canonical.source_url or '').encode()).hexdigest()[:16]}"
