"""Regression tests for cross-script Arab-society name dedup.

Two distinct bugs surfaced during the Feb 2026 blind sweep:

1. **``أبو`` / ``أم`` were being treated as Arabic honorifics and
   stripped pre-romanization**. In Arab-society Israeli naming these
   are integral surname components (Abu Rakik, Abu Rish, Abu Ghazala,
   Abu Freiha, Umm Kulthum). Stripping them on the Arabic side while
   the Hebrew side kept ``אבו`` made cross-script Jaro drop from
   ~0.85 to ~0.60 — silently splitting the same victim into two
   canonical cases.

2. **Hebrew letters used to represent Arabic consonants weren't being
   biased toward their Arabic Latin equivalents**:
     • ``ק`` rendered Arabic ``ق`` but anyascii output ``k`` not ``q``
     • ``ג'`` (gimel + geresh) rendered Arabic ``ج`` but anyascii
       output ``g`` not ``j``
   These mismatches blocked Najib (`ngyb`/`njyb`) and Rakik (`rkyk`/
   `rqyq`) cross-script merges.

Plus the reconciler's ``_token_containment_match`` was using literal
``set(short) < set(long_)`` comparison, which rejected fuzzy near-
matches like ``hsyn`` vs ``hwsyyn`` (Hebrew long-/i:/ rendered with
double yod). It now uses fuzzy per-token Jaro mirroring verify.
"""
from __future__ import annotations

from crime_pipeline.dedup.name_normalizer import (
    jaro_winkler_similarity,
    romanize_name,
)
from crime_pipeline.enrichment.reconciler import reconcile_cases
from crime_pipeline.verification import _verify_match


# ---------------------------------------------------------------------------
# Romanization: أبو preservation
# ---------------------------------------------------------------------------

def test_arabic_abu_is_not_stripped_as_honorific() -> None:
    """Arabic 'أبو' must stay in the romanized output. Before the fix
    'حسين أبو رقيق' romanized to 'hsyn rqyq' (no abu/bw) which
    couldn't match Hebrew 'חוסיין אבו סאלח רקיק' = 'hwsyyn bw slh rqyq'."""
    rom = romanize_name("حسين أبو رقيق")
    assert "bw" in rom, f"'bw' (romanized أبو) missing from {rom!r}"


def test_arabic_umm_is_not_stripped_as_honorific() -> None:
    """``أم`` is part of Arab-society family names (Umm Kulthum,
    Umm al-Fahm) — must not be stripped either."""
    rom = romanize_name("أم كلثوم")
    # Either 'am' or 'm' must remain — the alif may be silent post-anyascii
    # but the meem (m) must survive.
    assert "m" in rom


# ---------------------------------------------------------------------------
# Romanization: Hebrew → Arabic bias map extensions
# ---------------------------------------------------------------------------

def test_hebrew_qof_maps_to_arabic_qaf() -> None:
    """``ק`` rendering Arabic ``ق`` must romanize to 'q' not 'k'.
    Otherwise 'רקיק' = 'rkyk' ≠ Arabic 'رقيق' = 'rqyq'."""
    assert romanize_name("רקיק") == "rqyq"


def test_hebrew_gimel_geresh_maps_to_arabic_jeem() -> None:
    """The Hebrew digraph ``ג'`` (gimel + geresh) renders Arabic ``ج``
    and must romanize to 'j', not 'g'. Najib Abu Rish was the live
    failure: Hebrew 'נג'יב' = 'ngyb' ≠ Arabic 'نجيب' = 'njyb'."""
    assert romanize_name("נג'יב") == "njyb"
    assert romanize_name("יאסר חוג'יראת") == "ysr hwjyrt"
    assert romanize_name("מחמוד ג'אסר") == "mhmwd jsr"


# ---------------------------------------------------------------------------
# Cross-script Jaro for the live failure cases
# ---------------------------------------------------------------------------

def test_najib_abu_rish_cross_script_jaro_above_threshold() -> None:
    """Live Feb 2026 case: Hebrew 'נג'יב אבו ריש' vs Arabic 'نجيب حمد
    أبو ريش'. Must clear Jaro >= 0.85 so verify's direct-Jaro accept
    fires without falling through to containment."""
    j = jaro_winkler_similarity("נג'יב אבו ריש", "نجيب حمد أبو ريش")
    assert j >= 0.85, f"Najib cross-script Jaro = {j:.3f}, expected >= 0.85"


def test_yasser_hujirat_cross_script_jaro_high() -> None:
    """The Jan 2026 triple-murder case had Yasser Hujirat in three
    forms. Cross-script Jaro should now be near-perfect."""
    j = jaro_winkler_similarity("יאסר חוג'יראת", "ياسر حجيرات")
    assert j >= 0.95, f"Yasser cross-script Jaro = {j:.3f}, expected >= 0.95"


# ---------------------------------------------------------------------------
# Reconciler: end-to-end merge of the live failure pairs
# ---------------------------------------------------------------------------

def test_reconciler_merges_hussein_abu_rakik_cross_script() -> None:
    """The Hussein Abu Rakik (Lod) Feb 11/12 pair must merge despite
    the Hebrew 4-token form with extra middle name "Salah" and the
    Arabic 3-token form. Token-containment fuzzy match is the path."""
    he = {
        "victim_name_he": "חוסיין אבו סאלח רקיק",
        "city": "לוד",
        "incident_date": "2026-02-11",
        "sources": [{"url": "https://example.com/he"}],
    }
    ar = {
        "victim_name_ar": "حسين أبو رقيق",
        "city": "اللد",
        "incident_date": "2026-02-12",
        "sources": [{"url": "https://example.com/ar"}],
    }
    result = reconcile_cases([he, ar])
    assert result.cases_after == 1, (
        f"Expected 1 merged case, got {result.cases_after} — "
        f"merges: {result.merged_pairs}"
    )


def test_reconciler_merges_najib_abu_rish_cross_script() -> None:
    """Najib Abu Rish (Yarka) Feb 12: Arabic version adds middle name
    'Hamad'. Cities ירכא ↔ يركا must canonicalise via the gazetteer."""
    he = {
        "victim_name_he": "נג'יב אבו ריש",
        "city": "ירכא",
        "incident_date": "2026-02-12",
        "sources": [{"url": "https://example.com/he2"}],
    }
    ar = {
        "victim_name_ar": "نجيب حمد أبو ريش",
        "city": "يركا",
        "incident_date": "2026-02-12",
        "sources": [{"url": "https://example.com/ar2"}],
    }
    result = reconcile_cases([he, ar])
    assert result.cases_after == 1


# ---------------------------------------------------------------------------
# Verify: the same matches via the truth-vs-pipeline path
# ---------------------------------------------------------------------------

def test_verify_matches_hussein_cross_script() -> None:
    truth = {"victim_name_ar": "حسين أبو رقيق"}
    case = {"victim_name_he": "חוסיין אבו סאלח רקיק"}
    assert _verify_match(truth, case) is True


def test_verify_matches_najib_cross_script() -> None:
    truth = {"victim_name_ar": "نجيب أبو ريش"}
    case = {"victim_name_he": "נג'יב אבו ריש"}
    assert _verify_match(truth, case) is True


# ---------------------------------------------------------------------------
# Sanity: existing well-known cases must still work after the fixes
# ---------------------------------------------------------------------------

def test_bakr_yassin_cross_script_still_perfect() -> None:
    """The benchmark from the original Hebrew↔Arabic fix. Must stay 1.0."""
    j = jaro_winkler_similarity("בכר מחמוד יאסין", "بكر محمود ياسين")
    assert j >= 0.99


def test_father_son_nassar_still_rejected_post_fix() -> None:
    """The positional anchor on containment must still reject the
    Adham Nadhim Nassar (dad) ↔ Nadhim Nassar (son) ambiguity."""
    truth_son = {"victim_name_ar": "نظيم نصار"}
    case_dad = {"victim_name_ar": "أدهم نظيم نصار"}
    assert _verify_match(truth_son, case_dad) is False


# ---------------------------------------------------------------------------
# Gazetteer: new cities from the Feb 2026 sweep
# ---------------------------------------------------------------------------

def test_yarka_in_gazetteer_both_scripts() -> None:
    from crime_pipeline.utils.gazetteer import normalize_city
    ar = normalize_city("يركا")
    he = normalize_city("ירכא")
    assert ar is not None and he is not None
    assert ar["name_en"] == he["name_en"] == "Yarka"


def test_shaqib_al_salam_in_gazetteer() -> None:
    from crime_pipeline.utils.gazetteer import normalize_city
    he = normalize_city("שגב שלום")
    ar = normalize_city("شقيب السلام")
    assert he is not None and ar is not None
    assert he["name_en"] == ar["name_en"]


def test_fureidis_in_gazetteer() -> None:
    from crime_pipeline.utils.gazetteer import normalize_city
    assert normalize_city("פוריידיס") is not None
    assert normalize_city("الفريديس") is not None
