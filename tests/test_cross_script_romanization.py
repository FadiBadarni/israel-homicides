"""Regression tests for the cross-script Hebrew↔Arabic romanization fix.

The bug: ``romanize_name`` used ``anyascii`` directly, which follows
modern-Hebrew phonetics (ב→'v', כ→'kh', ו→'v'). For Arab-society victim
names appearing in BOTH Hebrew and Arabic news, this means the same
person's name produces different romanizations across scripts:

    Arabic 'بكر محمود ياسين' → 'bkr mhmwd ysyn'
    Hebrew 'בכר מחמוד יאסין'  → 'vkhr mhmvd ysyn'
    Jaro = 0.830 (below the verify 0.85 threshold → silent recall miss)

Fix: a small pre-anyascii character map (ב→b, כ→k, ו→w) aligned with
Arabic phonetics. Hebrew letters used to spell Arabic-origin names now
produce the same Latin as the Arabic source.
"""
from __future__ import annotations

from crime_pipeline.dedup.name_normalizer import (
    jaro_winkler_similarity,
    romanize_name,
)


# ---------------------------------------------------------------------------
# Same-person cross-script — MUST clear the 0.85 verify threshold
# ---------------------------------------------------------------------------

def test_bakr_cross_script_now_matches() -> None:
    """The headline regression case. Pre-fix Jaro=0.830; post-fix ≈1.0."""
    j = jaro_winkler_similarity("بكر محمود ياسين", "בכר מחמוד יאסין")
    assert j >= 0.95


def test_bakr_short_form_cross_script() -> None:
    j = jaro_winkler_similarity("بكر ياسين", "בכר יאסין")
    assert j >= 0.85


def test_magdi_cross_script() -> None:
    """Magdi was already above 0.85; sanity check we didn't break it."""
    j = jaro_winkler_similarity("مجدي عاطف شلاعطة", "מג'די עאטף שלאעטה")
    assert j >= 0.85


def test_khaled_cross_script() -> None:
    j = jaro_winkler_similarity("خالد غدير", "ח'אלד ע'דיר")
    assert j >= 0.85


# ---------------------------------------------------------------------------
# Different people — MUST stay below 0.85
# ---------------------------------------------------------------------------

def test_different_people_cross_script_does_not_match() -> None:
    """A random Arabic name vs a random Hebrew name must NOT collide."""
    j = jaro_winkler_similarity("بكر ياسين", "נורית בן דוד")
    assert j < 0.85


def test_different_people_same_script_arabic() -> None:
    j = jaro_winkler_similarity("بكر ياسين", "محمد علي حسن")
    assert j < 0.85


# ---------------------------------------------------------------------------
# Map content — invariants
# ---------------------------------------------------------------------------

def test_hebrew_bet_romanizes_to_b() -> None:
    """ב must now produce 'b' (not anyascii's default 'v')."""
    assert "b" in romanize_name("בכר")


def test_hebrew_kaf_romanizes_to_k() -> None:
    """כ must now produce 'k' (not 'kh')."""
    rom = romanize_name("כמיל")  # Kamil
    assert "k" in rom
    assert "kh" not in rom


def test_hebrew_vav_romanizes_to_w() -> None:
    """ו must now produce 'w' (not 'v')."""
    rom = romanize_name("וליד")  # Walid
    assert "w" in rom


def test_kaf_sofit_also_mapped() -> None:
    """Word-final kaf (ך) — same fix applies."""
    rom = romanize_name("מאלך")  # Malik
    assert "k" in rom
    assert "kh" not in rom


# ---------------------------------------------------------------------------
# Idempotency — Arabic-only input passes through unchanged
# ---------------------------------------------------------------------------

def test_arabic_only_name_unchanged() -> None:
    """Arabic names don't contain Hebrew letters; the map is a no-op for them."""
    assert romanize_name("بكر محمود ياسين") == "bkr mhmwd ysyn"


def test_empty_string_returns_empty() -> None:
    assert romanize_name("") == ""
