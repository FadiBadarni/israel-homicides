"""Regression test for reconciler intra-article exclusion.

The bug: after the multi-victim explode (1 article → N+1 virtual records),
the dedup stage clusters cross-article copies of the same victim
correctly. But the reconciler, running over the merged canonical cases,
saw distinct victims (e.g. Yasser/Kamel/Khaled from a triple murder),
noticed they shared the same city and similar Jaro on Hebrew/Arabic
romanizations, and collapsed them into one case with the others as
aliases — undoing the multi-victim explode.

Fix: if two cases share ANY source URL, refuse to merge them. The only
way one URL appears in two cases is via the multi-victim explode →
they're DIFFERENT victims by construction.
"""
from __future__ import annotations

from crime_pipeline.enrichment.reconciler import reconcile_cases


_SHARED_URL = "https://www.ynet.co.il/news/article/triple_murder_001"


def test_reconciler_refuses_to_merge_cases_sharing_source_url() -> None:
    """Two cases citing the same source URL must NOT merge, even when
    Jaro on names is high (Hebrew/Arabic transliteration variants of
    the same family name across the multi-victim article)."""
    case_a = {
        "victim_name_he": "יאסר חוג'יראת",
        "city": "שפרעם",
        "incident_date": "2026-01-07",
        "sources": [{"url": _SHARED_URL, "source_name": "ynet"}],
    }
    case_b = {
        "victim_name_he": "כאמל חוג'יראת",   # different victim, same family name
        "city": "שפרעם",
        "incident_date": "2026-01-07",
        "sources": [{"url": _SHARED_URL, "source_name": "ynet"}],
    }
    res = reconcile_cases([case_a, case_b])
    assert res.cases_after == 2, (
        "reconciler must keep both victims distinct when they share a "
        "source URL (multi-victim explode siblings)"
    )
    assert res.merged_pairs == []


def test_reconciler_still_merges_when_no_shared_url() -> None:
    """Sanity check: legitimate cross-source merges still work. Two
    cases describing the same victim from DIFFERENT articles must
    still reconcile."""
    case_a = {
        "victim_name_ar": "بكر محمود ياسين",
        "city": "عرابة",
        "incident_date": "2026-01-03",
        "sources": [{"url": "https://www.arab48.com/x/y", "source_name": "arab48"}],
    }
    case_b = {
        "victim_name_he": "בכר מחמוד יאסין",
        "city": "עראבה",
        "incident_date": "2026-01-03",
        "sources": [{"url": "https://www.ynet.co.il/a/b", "source_name": "ynet"}],
    }
    res = reconcile_cases([case_a, case_b])
    assert res.cases_after == 1, (
        "reconciler must still merge cross-source duplicates of the "
        "same victim (different URLs)"
    )
    assert len(res.merged_pairs) == 1


def test_reconciler_three_way_multi_victim_stays_distinct() -> None:
    """The actual live failure: 3 victims from a triple-murder article
    must all stay distinct. Without the URL-overlap guard the reconciler
    collapsed Yasser/Kamel/Khaled into one case with the other two as
    aliases (and even pulled in an unrelated victim's name)."""
    sources = [{"url": _SHARED_URL, "source_name": "arab48"}]
    cases = [
        {"victim_name_ar": "ياسر حجيرات", "city": "عرابة", "sources": sources},
        {"victim_name_ar": "كامل حجيرات", "city": "عرابة", "sources": sources},
        {"victim_name_ar": "خالد غدير",   "city": "عرابة", "sources": sources},
    ]
    res = reconcile_cases(cases)
    assert res.cases_after == 3
    assert res.merged_pairs == []


def test_reconciler_partial_overlap_blocks_merge() -> None:
    """A case can cite multiple sources; if ONE source URL overlaps,
    the merge is still blocked. Better to keep two distinct cases than
    to silently collapse them on the assumption the LLM was right both
    times."""
    cases = [
        {
            "victim_name_he": "יאסר חוג'יראת",
            "city": "שפרעם",
            "sources": [
                {"url": _SHARED_URL,                       "source_name": "ynet"},
                {"url": "https://other.tld/yasir_only",    "source_name": "mako"},
            ],
        },
        {
            "victim_name_he": "כאמל חוג'יראת",
            "city": "שפרעם",
            "sources": [
                {"url": _SHARED_URL,                       "source_name": "ynet"},
                {"url": "https://other.tld/kamel_only",    "source_name": "channel13"},
            ],
        },
    ]
    res = reconcile_cases(cases)
    assert res.cases_after == 2
