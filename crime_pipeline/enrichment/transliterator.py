"""Deterministic transliteration of Arab-society names across scripts.

Used by the post-merge name-enrichment step to fill ``victim_name_*``
fields where only one script was source-attested. Results are written
to a separate ``name_transliterations`` list with explicit provenance
— never into the source-of-truth fields.

Two-level resolution:

1. **Dictionary lookup** (``data/name_dictionary.json``). Per-token
   mappings for common Israeli Arab / Bedouin name components. Used
   when present because rule-based char maps are lossy for vowels.

2. **Rule-based char maps**. Fallback for tokens not in the dictionary.
   Char-by-char substitution using the same conventions as the
   existing romanizer's bias map. Covers any name shape but produces
   "blender" transliterations for unfamiliar tokens — the UI's
   "inferred" badge is doing real work here.

Coverage:
  ar → he   bidirectional Arabic/Hebrew char map
  he → ar   reverse direction
  ar → en   anyascii + vowel-injection heuristic
  he → en   anyascii + the existing Hebrew-bias map
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Char maps
# ---------------------------------------------------------------------------

# Arabic → Hebrew. Single-codepoint deterministic substitutions for the
# Arab-society naming convention. Edge cases (ث ↔ ת/ث alternatives, ذ
# ↔ ד'/ز) collapse to the most-common Israeli-Arab spelling. The
# dictionary handles exceptions per name.
_AR_TO_HE: dict[str, str] = {
    "ا": "א", "أ": "א", "إ": "א", "آ": "א", "ٱ": "א",
    "ب": "ב",
    "ت": "ת",
    "ث": "ת",       # almost always ת in modern Israeli-Arab names
    "ج": "ג'",       # gimel + geresh for the J sound (Hebrew convention)
    "ح": "ח",
    "خ": "ח'",
    "د": "ד",
    "ذ": "ד'",
    "ر": "ר",
    "ز": "ז",
    "س": "ס",
    "ش": "ש",
    "ص": "צ",
    "ض": "ד",       # commonly written ד in Israeli-Arab names
    "ط": "ט",
    "ظ": "ז'",
    "ع": "ע",
    "غ": "ע'",
    "ف": "פ",
    "ق": "ק",
    "ك": "כ",
    "ل": "ל",
    "م": "מ",
    "ن": "נ",
    "ه": "ה",
    "ة": "ה",       # taa marbuta at end of word
    "و": "ו",
    "ي": "י",
    "ى": "א",       # alif maksura
    "ء": "",         # hamza usually dropped in Hebrew transliteration
    "ئ": "י",
    "ؤ": "ו",
    " ": " ",
    "-": "-",
}

# Hebrew → Arabic. Reverse direction. Several Hebrew characters map to
# multiple Arabic options (ש → ش/س, ת → ت/ث); we pick the most common
# for Israeli-Arab names and let the dictionary handle exceptions.
_HE_TO_AR: dict[str, str] = {
    "א": "ا",
    "ב": "ب",
    "ג": "ج",        # bare gimel — rare in Arab names; default to ج
    "ד": "د",
    "ה": "ه",
    "ו": "و",
    "ז": "ز",
    "ח": "ح",
    "ט": "ط",
    "י": "ي",
    "כ": "ك",
    "ך": "ك",
    "ל": "ل",
    "מ": "م",
    "ם": "م",
    "נ": "ن",
    "ן": "ن",
    "ס": "س",
    "ע": "ع",
    "פ": "ف",
    "ף": "ف",
    "צ": "ص",
    "ץ": "ص",
    "ק": "ق",
    "ר": "ر",
    "ש": "ش",        # شو > سو in Arab-society names
    "ת": "ت",        # ت > ث for modern Israeli-Arab names
    " ": " ",
    "-": "-",
}

# Hebrew digraphs (gimel+geresh = j, etc.) handled BEFORE the
# char-by-char map. Order matters — process digraphs first or the
# bare gimel rule eats them.
_HE_DIGRAPHS: list[tuple[str, str]] = [
    ("ג'", "ج"),
    ("ז'", "ج"),     # alternative for ج (rare)
    ("ד'", "ذ"),
    ("ח'", "خ"),
    ("ע'", "غ"),
    ("צ'", "ض"),     # rare
    ("ת'", "ث"),     # rare
]


# ---------------------------------------------------------------------------
# Dictionary
# ---------------------------------------------------------------------------

_DEFAULT_DICT_PATH = Path("data/name_dictionary.json")


@lru_cache(maxsize=1)
def _load_dictionary() -> dict[str, dict[str, str]]:
    """Load the name-component dictionary. Cached for the process.

    Schema: ``{ "<arabic-token>": { "he": "...", "en": "..." }, ... }``.
    Arabic is the canonical key because most of our source data is
    Arabic; reverse lookups (Hebrew→Arabic) build a reverse index
    on demand below.
    """
    if not _DEFAULT_DICT_PATH.exists():
        return {}
    try:
        return json.loads(_DEFAULT_DICT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


@lru_cache(maxsize=1)
def _reverse_dictionary(target_script: str) -> dict[str, str]:
    """Index from ``target_script`` value back to the canonical Arabic
    key. Used for he→ar lookups (where the dictionary key is Arabic
    but we need to find the Arabic given a Hebrew form)."""
    src = _load_dictionary()
    out: dict[str, str] = {}
    for ar_key, mapping in src.items():
        v = mapping.get(target_script)
        if v:
            out[v] = ar_key
    return out


# ---------------------------------------------------------------------------
# Transliteration core
# ---------------------------------------------------------------------------

def _apply_char_map(text: str, mapping: dict[str, str]) -> str:
    """Apply a single-codepoint char map. Unknown codepoints pass
    through unchanged (better than dropping silently — surfaces gaps)."""
    return "".join(mapping.get(c, c) for c in text)


def _ar_to_he(text: str) -> str:
    return _apply_char_map(text, _AR_TO_HE)


def _he_to_ar(text: str) -> str:
    """Hebrew → Arabic with digraph pre-pass for ג' / ז' / ד' / ח' / ע'."""
    for src, dst in _HE_DIGRAPHS:
        text = text.replace(src, dst)
    return _apply_char_map(text, _HE_TO_AR)


def _to_latin(text: str, source_script: str) -> str:
    """Best-effort transliteration to Latin via the existing romanizer,
    plus a Title-case + naive vowel-injection pass so the output looks
    like a readable English name.

    Example: 'بكر ياسين' → 'Bakr Yassin' (not 'bkr ysyn').
    """
    from crime_pipeline.dedup.name_normalizer import romanize_name

    rom = romanize_name(text)
    if not rom:
        return ""
    # Inject naive vowels between consonants if the token has 2-3
    # consecutive consonants. This is a heuristic — the dictionary
    # handles canonical spellings for known names.
    out_tokens = []
    for tok in rom.split():
        # Title-case each token
        out_tokens.append(_naive_vowel_inject(tok).title())
    return " ".join(out_tokens)


def _naive_vowel_inject(token: str) -> str:
    """Insert an 'a' between consecutive consonants. Cheap heuristic
    to make romanizer output more readable. NOT linguistically
    correct — dictionary entries should override for accuracy."""
    vowels = set("aeiou")
    out: list[str] = []
    for i, ch in enumerate(token):
        out.append(ch)
        if i < len(token) - 1:
            nxt = token[i + 1]
            if ch not in vowels and nxt not in vowels:
                out.append("a")
    return "".join(out)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def transliterate(
    text: str,
    source_script: str,
    target_script: str,
) -> Optional[tuple[str, str]]:
    """Transliterate ``text`` from ``source_script`` into
    ``target_script``. Returns ``(value, method)`` where method is
    ``"dictionary"`` or ``"rule_based"``, or ``None`` if input is
    empty / scripts are equal.

    Per-token dictionary lookup first (so well-known names hit their
    canonical spelling); fall back to char-by-char rules for unknown
    tokens. The output value blends both — known tokens use the
    dictionary, unknown ones use the rules.
    """
    if not text or not text.strip():
        return None
    if source_script == target_script:
        return None
    if source_script not in {"ar", "he"} or target_script not in {"ar", "he", "en"}:
        return None

    tokens = text.split()
    if not tokens:
        return None

    dict_data = _load_dictionary()
    if source_script == "he":
        reverse_idx = _reverse_dictionary("he")
    else:
        reverse_idx = {}

    out_tokens: list[str] = []
    used_dictionary = False
    for tok in tokens:
        # Try dictionary first
        ar_key: Optional[str] = None
        if source_script == "ar" and tok in dict_data:
            ar_key = tok
        elif source_script == "he" and tok in reverse_idx:
            ar_key = reverse_idx[tok]

        if ar_key is not None:
            mapping = dict_data.get(ar_key) or {}
            if target_script == "ar":
                out_tokens.append(ar_key)
                used_dictionary = True
                continue
            v = mapping.get(target_script)
            if v:
                out_tokens.append(v)
                used_dictionary = True
                continue

        # Fall back to rule-based char map
        if source_script == "ar" and target_script == "he":
            out_tokens.append(_ar_to_he(tok))
        elif source_script == "he" and target_script == "ar":
            out_tokens.append(_he_to_ar(tok))
        elif target_script == "en":
            out_tokens.append(_to_latin(tok, source_script))
        else:
            return None  # shouldn't reach here

    value = " ".join(t for t in out_tokens if t).strip()
    if not value:
        return None
    method = "dictionary" if used_dictionary else "rule_based"
    return value, method
