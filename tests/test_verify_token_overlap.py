"""Regression tests for the verify token-overlap rule.

The bug: a flat Jaro-Winkler ≥ 0.85 threshold accepts the Bakr↔Bakr-Mahmoud
substring match (legit) but ALSO accepts family-name collisions:
  'أحمد نصّار' vs 'نظيم نصار' → Jaro ≈ 0.878 → false positive

Fix: in the ambiguous Jaro zone [0.85, 0.95), require ≥ 2 shared tokens.
'Bakr Yassin' / 'Bakr Mahmoud Yassin' share 2 tokens (bakr + yassin) ✓
'Ahmed Nassar' / 'Nathim Nassar' share 1 token (nassar only) ✗
"""
from __future__ import annotations

from crime_pipeline.verification import _verify_match


# ---------------------------------------------------------------------------
# Family-name collisions — MUST be rejected
# ---------------------------------------------------------------------------

def test_ahmed_vs_nathim_nassar_does_not_match() -> None:
    """The actual live false positive from the January-truth verify."""
    truth = {"victim_name_ar": "نظيم نصار"}
    case = {"victim_name_ar": "أحمد نصّار"}
    assert _verify_match(truth, case) is False


def test_mohammed_ali_vs_ahmed_ali_does_not_match() -> None:
    """Different given names, same family — single shared token, reject."""
    truth = {"victim_name_ar": "محمد علي"}
    case = {"victim_name_ar": "أحمد علي"}
    assert _verify_match(truth, case) is False


def test_english_johns_smith_collision() -> None:
    """Generic family-name pattern in Latin."""
    truth = {"victim_name_en": "John Smith"}
    case = {"victim_name_en": "Sam Smith"}
    # Both names share 'smith' but nothing else — should reject
    assert _verify_match(truth, case) is False


# ---------------------------------------------------------------------------
# Real matches — MUST be accepted
# ---------------------------------------------------------------------------

def test_bakr_substring_match_still_works() -> None:
    """The other ambiguous-zone case: 'Bakr Yassin' ⊂ 'Bakr Mahmoud Yassin'.
    Shares 2 tokens (bakr + yassin) so accepted."""
    truth = {"victim_name_ar": "بكر ياسين"}
    case = {"victim_name_ar": "بكر محمود ياسين"}
    assert _verify_match(truth, case) is True


def test_identical_name_accepted() -> None:
    """Jaro = 1.0 sits above the high threshold, auto-accept."""
    truth = {"victim_name_ar": "بكر محمود ياسين"}
    case = {"victim_name_ar": "بكر محمود ياسين"}
    assert _verify_match(truth, case) is True


def test_one_char_typo_still_matches() -> None:
    """'mahmoud' vs 'mhmd' typo — Jaro ~0.92 + 3 shared tokens → accept."""
    truth = {"victim_name_ar": "بكر محمود ياسين"}
    case = {"victim_name_ar": "بكر محمد ياسين"}
    assert _verify_match(truth, case) is True


def test_cross_script_bakr_still_matches() -> None:
    """Cross-script (post-romanization fix) Jaro=1.0 — auto-accept."""
    truth = {"victim_name_ar": "بكر محمود ياسين"}
    case = {"victim_name_he": "בכר מחמוד יאסין"}
    assert _verify_match(truth, case) is True


def test_two_token_full_match_accepted() -> None:
    """Two-token name, identical → Jaro=1.0 auto-accept."""
    truth = {"victim_name_ar": "محمد علي"}
    case = {"victim_name_ar": "محمد علي"}
    assert _verify_match(truth, case) is True


# ---------------------------------------------------------------------------
# Single-token name edge cases
# ---------------------------------------------------------------------------

def test_single_token_identical_matches() -> None:
    """One-word name on both sides, identical — Jaro = 1.0 auto-accept."""
    truth = {"victim_name_ar": "أحمد"}
    case = {"victim_name_ar": "أحمد"}
    assert _verify_match(truth, case) is True


def test_single_token_vs_multi_token_rejected_in_ambiguous_zone() -> None:
    """'بكر' vs 'بكر محمود ياسين' — Jaro in ambiguous zone but only 1
    shared token. Token-overlap rule rejects."""
    truth = {"victim_name_ar": "بكر"}
    case = {"victim_name_ar": "بكر محمود ياسين"}
    # Jaro between 'bkr' and 'bkr mhmwd ysyn' is in the [0.85, 0.95) range
    # (Winkler prefix bonus on 'bkr'). Token overlap = 1. → reject.
    result = _verify_match(truth, case)
    # Verify it's at least not auto-accepting the partial match alone
    # (a flat Jaro=0.85 rule would accept this — token rule rejects)
    assert result is False
