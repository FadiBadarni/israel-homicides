"""
Main deduplication orchestrator for the crime-news pipeline.

Decision architecture (debate-mandated):
  1. Blocking on (city, YYYY-MM)          — reduces O(n²) to O(k²)
  2. Jaro-Winkler on romanized names      — PRE-FILTER only (threshold 0.88)
  3. Multilingual cosine similarity       — DECISION GATE (threshold 0.82)

Embedding model: paraphrase-multilingual-MiniLM-L12-v2
Storage backend: DuckDB (avoids SQLite quadratic write bottleneck)
"""

from __future__ import annotations

import numpy as np
import structlog

from .embedder import ArticleEmbedder
from .graph import DeduplicationGraph
from .name_normalizer import jaro_winkler_similarity

log = structlog.get_logger()

# Lower index = higher trust; used as tie-breaker when choosing canonical record
SOURCE_PRIORITY: dict[str, int] = {
    "police": 0,
    "ynet": 1,
    "panet": 2,
}

# Cosine zone boundaries
_COSINE_MERGE_THRESHOLD = 0.82   # at or above → merge (if jaro also passes)
_COSINE_REVIEW_LOW = 0.70        # [0.70, 0.82) → ambiguous → human review


class Deduplicator:
    """
    End-to-end deduplication orchestrator.

    Parameters
    ----------
    jaro_threshold:
        Romanized Jaro-Winkler score above which the name pair is considered
        consistent.  Used as a PRE-FILTER — it cannot alone trigger a merge.
    cosine_threshold:
        Multilingual cosine similarity above which records are merged (subject
        to jaro passing or one name being absent).  This is the DECISION GATE.
    duckdb_path:
        Path for the DuckDB database file.  Defaults to ":memory:" for tests
        and transient pipeline runs.
    """

    def __init__(
        self,
        jaro_threshold: float = 0.88,
        cosine_threshold: float = 0.82,
        duckdb_path: str = ":memory:",
    ) -> None:
        self.jaro_threshold = jaro_threshold
        self.cosine_threshold = cosine_threshold
        self.embedder = ArticleEmbedder()
        self.graph = DeduplicationGraph(duckdb_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, records: list[dict]) -> dict:
        """
        Deduplicate a batch of records.

        Parameters
        ----------
        records:
            List of dicts, each with keys:
                id              str   – unique record identifier
                victim_name     str   – may be None / empty
                incident_date   str   – ISO date string (YYYY-MM-DD or prefix)
                city            str   – incident city
                article_text    str   – full article body used for embeddings
                source          str   – source slug (e.g. 'police', 'ynet')
                confidence_score float – extraction confidence [0, 1]

        Returns
        -------
        dict with keys:
            clusters      list[list[str]]              – merged record ID groups
            singletons    list[str]                    – unmerged record IDs
            review_pairs  list[tuple[str,str,float,float]] – (a_id, b_id, jaro, cosine)
        """
        if len(records) < 2:
            return {
                "clusters": [],
                "singletons": [r["id"] for r in records],
                "review_pairs": [],
            }

        log.info("dedup_start", num_records=len(records))

        # ── Step 1: Embed all article texts ────────────────────────────
        texts = [r.get("article_text") or "" for r in records]
        embeddings = self.embedder.embed_texts(texts)

        # ── Step 2: Blocking — reduce candidate pairs ───────────────────
        candidate_pairs = self.graph.get_block_candidates(records)
        log.info(
            "dedup_blocking",
            total_records=len(records),
            candidate_pairs=len(candidate_pairs),
            max_possible=len(records) * (len(records) - 1) // 2,
        )

        review_pairs: list[tuple[str, str, float, float]] = []
        edges_to_save: list[dict] = []

        # ── Step 3: Score each candidate pair ──────────────────────────
        for i, j in candidate_pairs:
            rec_a, rec_b = records[i], records[j]

            # Jaro-Winkler on romanized victim names (pre-filter only)
            jaro_score = jaro_winkler_similarity(
                rec_a.get("victim_name"),
                rec_b.get("victim_name"),
            )

            # Cosine similarity — decision gate
            cosine_score = float(np.dot(embeddings[i], embeddings[j]))

            # Block key for provenance / debugging
            city = (rec_a.get("city") or "").lower().strip()
            month = (rec_a.get("incident_date") or "")[:7]
            block_key = f"{city}|{month}"

            decision = self._decide(rec_a, rec_b, jaro_score, cosine_score)

            if decision == "review":
                review_pairs.append((rec_a["id"], rec_b["id"], jaro_score, cosine_score))

            edges_to_save.append(
                {
                    "record_a_id": rec_a["id"],
                    "record_b_id": rec_b["id"],
                    "jaro_score": jaro_score,
                    "cosine_score": cosine_score,
                    "decision": decision,
                    "block_key": block_key,
                }
            )

            log.debug(
                "dedup_pair",
                a=rec_a["id"][:8],
                b=rec_b["id"][:8],
                jaro=round(jaro_score, 3),
                cosine=round(cosine_score, 3),
                decision=decision,
            )

        # Bulk insert to DuckDB (single transaction — much faster than one-by-one)
        if edges_to_save:
            self.graph.bulk_save_edges(edges_to_save)

        # ── Step 4: Resolve merge clusters via union-find ───────────────
        clusters = self.graph.get_merge_clusters()
        clustered_ids = {rid for cluster in clusters for rid in cluster}
        singletons = [r["id"] for r in records if r["id"] not in clustered_ids]

        log.info(
            "dedup_complete",
            clusters=len(clusters),
            singletons=len(singletons),
            review_pairs=len(review_pairs),
            edges_merge=self.graph.edge_count("merge"),
            edges_distinct=self.graph.edge_count("distinct"),
            edges_review=self.graph.edge_count("review"),
        )

        return {
            "clusters": clusters,
            "singletons": singletons,
            "review_pairs": review_pairs,
        }

    def select_canonical(self, cluster: list[str], records: list[dict]) -> str:
        """
        Choose the canonical (authoritative) record from a merged cluster.

        Selection order:
          1. Source priority tier (police > ynet > panet > unknown)
          2. Higher confidence score as tie-breaker (descending)

        Parameters
        ----------
        cluster:
            List of record IDs belonging to the same cluster.
        records:
            Full record list (used to look up metadata by ID).

        Returns
        -------
        The ID of the canonical record.
        """
        cluster_set = set(cluster)
        cluster_records = [r for r in records if r["id"] in cluster_set]
        if not cluster_records:
            raise ValueError(f"No records found for cluster: {cluster}")

        best = min(
            cluster_records,
            key=lambda r: (
                SOURCE_PRIORITY.get(r.get("source", ""), 99),
                -(r.get("confidence_score") or 0.0),
            ),
        )
        return best["id"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _decide(
        self,
        rec_a: dict,
        rec_b: dict,
        jaro_score: float,
        cosine_score: float,
    ) -> str:
        """
        Apply the two-gate decision logic and return one of:
            'merge'    – records describe the same real-world event/victim
            'distinct' – records are clearly different
            'review'   – ambiguous; requires human adjudication
        """
        name_a = rec_a.get("victim_name")
        name_b = rec_b.get("victim_name")
        either_name_missing = not name_a or not name_b

        # Token-subset check: {"יאסין"} ⊆ {"בכר","יאסין"} — surname-only extraction.
        # When one name's tokens are a strict subset of the other's, treat the names
        # as consistent. Requires cosine ≥ review low-bound to avoid false positives.
        name_subset_match = False
        if name_a and name_b:
            toks_a = set(name_a.split())
            toks_b = set(name_b.split())
            if toks_a < toks_b or toks_b < toks_a:
                name_subset_match = True

        if cosine_score >= self.cosine_threshold:
            # GATE PASSED: high semantic similarity
            if jaro_score >= self.jaro_threshold or either_name_missing or name_subset_match:
                return "merge"
            else:
                # Cosine says same event but names differ significantly
                # Could be a different victim at same incident; flag for review
                return "review"

        if cosine_score >= _COSINE_REVIEW_LOW:
            # Subset name + mid cosine → safe to merge (surname-only extraction pattern)
            if name_subset_match:
                return "merge"
            return "review"

        # Low cosine — distinct records
        return "distinct"

    def close(self) -> None:
        """Release DuckDB connection."""
        self.graph.close()

    def __enter__(self) -> "Deduplicator":
        return self

    def __exit__(self, *_) -> None:
        self.close()
