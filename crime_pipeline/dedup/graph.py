import uuid
from collections import defaultdict

import duckdb
import structlog

log = structlog.get_logger()


class DeduplicationGraph:
    """
    DuckDB-backed deduplication edge store with union-find cluster resolution.

    DuckDB is used instead of SQLite to avoid the quadratic write bottleneck
    that SQLite exhibits under bulk INSERT workloads with large candidate sets.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.conn = duckdb.connect(db_path)
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS dedup_edges (
                id              VARCHAR PRIMARY KEY,
                record_a_id     VARCHAR NOT NULL,
                record_b_id     VARCHAR NOT NULL,
                jaro_score      FLOAT,
                cosine_score    FLOAT,
                decision        VARCHAR NOT NULL,   -- 'merge' | 'distinct' | 'review'
                block_key       VARCHAR,
                decided_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS merge_clusters (
                cluster_id          VARCHAR NOT NULL,
                record_id           VARCHAR NOT NULL,
                is_canonical        BOOLEAN DEFAULT FALSE,
                canonical_priority  INTEGER DEFAULT 0
            )
        """)

    # ------------------------------------------------------------------
    # Blocking
    # ------------------------------------------------------------------

    def get_block_candidates(self, records: list[dict]) -> list[tuple[int, int]]:
        """
        Return candidate pairs as (i, j) index tuples where i < j.

        Primary blocking key: (city.lower(), incident_date[:7]) → YYYY-MM grain.
        Records lacking city or date fall into '__no_block__' and are only
        paired with each other via the primary key.

        Secondary blocking key: romanized name prefix (first 6 non-space chars).
        This catches breaking-news articles that have no city/date yet but share
        a victim name with a later, more complete article (e.g. Bakr Yassin:
        Channel 13 breaking blurbs vs the Haaretz confirmed-death piece).

        Complexity: O(k²) per block, where k << n.
        """
        blocks: dict[str, list[int]] = defaultdict(list)

        try:
            from crime_pipeline.dedup.name_normalizer import romanize_name as _romanize
        except Exception:
            _romanize = None

        for idx, rec in enumerate(records):
            city = (rec.get("city") or "").lower().strip()
            date = (rec.get("incident_date") or "")[:7]  # YYYY-MM
            if city and date:
                blocks[f"{city}|{date}"].append(idx)
            else:
                blocks["__no_block__"].append(idx)

            # Secondary: per-token name blocks so partial names (e.g. surname-only
            # "Yasin") are paired with full names ("Bakr Yasin") that share any token.
            name = rec.get("victim_name") or ""
            if name and _romanize is not None:
                try:
                    romanized = _romanize(name).lower()
                    for token in romanized.split():
                        if len(token) >= 3:
                            blocks[f"nametok|{token[:8]}"].append(idx)
                except Exception:
                    pass

        pairs: set[tuple[int, int]] = set()
        for indices in blocks.values():
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    lo, hi = min(indices[a], indices[b]), max(indices[a], indices[b])
                    pairs.add((lo, hi))

        return list(pairs)

    # ------------------------------------------------------------------
    # Edge persistence
    # ------------------------------------------------------------------

    def save_edge(
        self,
        record_a_id: str,
        record_b_id: str,
        jaro_score: float,
        cosine_score: float,
        decision: str,
        block_key: str | None = None,
    ) -> None:
        """Persist a single dedup edge to DuckDB."""
        edge_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO dedup_edges
                (id, record_a_id, record_b_id, jaro_score, cosine_score, decision, block_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [edge_id, record_a_id, record_b_id, jaro_score, cosine_score, decision, block_key],
        )

    def bulk_save_edges(self, edges: list[dict]) -> None:
        """
        Insert multiple edges in a single DuckDB transaction for throughput.

        Each dict must have keys: record_a_id, record_b_id, jaro_score,
        cosine_score, decision, block_key (optional).
        """
        rows = [
            (
                str(uuid.uuid4()),
                e["record_a_id"],
                e["record_b_id"],
                e.get("jaro_score"),
                e.get("cosine_score"),
                e["decision"],
                e.get("block_key"),
            )
            for e in edges
        ]
        self.conn.executemany(
            """
            INSERT INTO dedup_edges
                (id, record_a_id, record_b_id, jaro_score, cosine_score, decision, block_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    # ------------------------------------------------------------------
    # Cluster resolution via Union-Find
    # ------------------------------------------------------------------

    def get_merge_clusters(self) -> list[list[str]]:
        """
        Return connected components of all 'merge' edges as clusters of record IDs.

        Uses path-compressed union-find for O(α(n)) amortized per operation.
        """
        edges = self.conn.execute(
            "SELECT record_a_id, record_b_id FROM dedup_edges WHERE decision = 'merge'"
        ).fetchall()

        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            # Path compression
            root = x
            while parent[root] != root:
                root = parent[root]
            # Compress path
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        def union(x: str, y: str) -> None:
            parent[find(x)] = find(y)

        all_ids: set[str] = set()
        for a, b in edges:
            all_ids.update([a, b])
            union(a, b)

        clusters: dict[str, list[str]] = {}
        for rid in all_ids:
            root = find(rid)
            clusters.setdefault(root, []).append(rid)

        return list(clusters.values())

    def get_review_edges(self) -> list[dict]:
        """Return all edges flagged for human review."""
        rows = self.conn.execute(
            """
            SELECT record_a_id, record_b_id, jaro_score, cosine_score, block_key, decided_at
            FROM dedup_edges
            WHERE decision = 'review'
            ORDER BY cosine_score DESC
            """
        ).fetchall()
        return [
            {
                "record_a_id": r[0],
                "record_b_id": r[1],
                "jaro_score": r[2],
                "cosine_score": r[3],
                "block_key": r[4],
                "decided_at": r[5],
            }
            for r in rows
        ]

    def edge_count(self, decision: str | None = None) -> int:
        """Return count of edges, optionally filtered by decision label."""
        if decision:
            return self.conn.execute(
                "SELECT COUNT(*) FROM dedup_edges WHERE decision = ?", [decision]
            ).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM dedup_edges").fetchone()[0]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DeduplicationGraph":
        return self

    def __exit__(self, *_) -> None:
        self.close()
