"""Test the dedup intra-article exclusion rule.

When the multi-victim explode step emits N virtual records from the same
article (e.g. a triple murder), every record has identical article_text
so the cosine-similarity gate would otherwise collapse all N into one
cluster — silently destroying every victim except the first.

The fix: _decide() short-circuits to 'distinct' when both records share
the same article_id, before the cosine/Jaro logic runs. Same-article
records can never merge with each other but can still merge with
records from other articles.
"""
from __future__ import annotations

from crime_pipeline.dedup.deduplicator import Deduplicator


def _make_dedup() -> Deduplicator:
    """Construct a Deduplicator instance for direct _decide() testing.
    The threshold values match the production defaults but are not
    exercised by the tests below — _decide() only cares about
    article_id when intra-article."""
    return Deduplicator(jaro_threshold=0.88, cosine_threshold=0.82)


def test_same_article_pair_rejected_at_cosine_1() -> None:
    """The exact pathology: cosine=1.0 (identical bodies), Jaro=1.0
    (identical names — shouldn't happen but defends against bugs), but
    article_id matches → reject."""
    d = _make_dedup()
    rec_a = {"article_id": "art_xyz", "victim_name": "primary"}
    rec_b = {"article_id": "art_xyz", "victim_name": "primary"}
    try:
        result = d._decide(rec_a, rec_b, jaro_score=1.0, cosine_score=1.0)
        assert result == "distinct"
    finally:
        d.close()


def test_different_articles_still_merge_on_high_similarity() -> None:
    """Sanity check the rule didn't accidentally break legit cross-source
    merging. Two records from DIFFERENT articles with high Jaro + cosine
    must still merge."""
    d = _make_dedup()
    rec_a = {"article_id": "art_ynet_001", "victim_name": "bkr ysyn"}
    rec_b = {"article_id": "art_arab48_002", "victim_name": "bkr ysyn"}
    try:
        result = d._decide(rec_a, rec_b, jaro_score=0.95, cosine_score=0.90)
        assert result == "merge"
    finally:
        d.close()


def test_missing_article_id_falls_through() -> None:
    """Records without article_id (legacy / hand-crafted test inputs)
    must not trigger the intra-article rule. The pair is then evaluated
    on Jaro/cosine like before."""
    d = _make_dedup()
    rec_a = {"victim_name": "alice"}
    rec_b = {"victim_name": "bob"}
    try:
        # Low cosine → distinct
        result = d._decide(rec_a, rec_b, jaro_score=0.3, cosine_score=0.2)
        assert result == "distinct"
    finally:
        d.close()


def test_same_article_pair_rejected_even_when_names_differ() -> None:
    """The triple-murder shape: 3 distinct named victims from one
    article, all sharing the same article_id. Cosine ≈ 1.0 (same body),
    but Jaro on the different names is low. Must still reject."""
    d = _make_dedup()
    rec_a = {"article_id": "triple_murder_art", "victim_name": "yasir hjirat"}
    rec_b = {"article_id": "triple_murder_art", "victim_name": "khald ghdyr"}
    try:
        result = d._decide(rec_a, rec_b, jaro_score=0.4, cosine_score=1.0)
        assert result == "distinct"
    finally:
        d.close()
