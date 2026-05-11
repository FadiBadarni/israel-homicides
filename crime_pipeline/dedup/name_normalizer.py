import re
import unicodedata
from anyascii import anyascii

# Arabic tashkeel (diacritical marks) unicode range
ARABIC_DIACRITICS = re.compile(r'[ً-ٟؐ-ؚۖ-ۜ]')

# Common honorifics to strip before comparison
HONORIFICS = {
    "ar": ["الشهيد", "المرحوم", "المغفور له", "الحاج", "أبو", "أم"],
    "he": ['ז"ל', 'הי"ד', "ר'", "רב"],
}


def normalize_arabic(text: str) -> str:
    """Strip diacritics, normalize Unicode to NFC, handle presentation forms."""
    # NFKC maps Arabic Presentation Forms-B (FE70-FEFF) to basic Arabic block
    text = unicodedata.normalize("NFKC", text)
    # Strip tashkeel and diacritical marks
    text = ARABIC_DIACRITICS.sub("", text)
    return text.strip()


def strip_honorifics(name: str) -> str:
    """Remove common honorifics from name strings."""
    for lang_honorifics in HONORIFICS.values():
        for h in lang_honorifics:
            name = name.replace(h, "").strip()
    return name


# Pre-anyascii Hebrew→Latin overrides aligned with Arabic phonetics.
#
# Modern-Hebrew transliteration (which ``anyascii`` follows) maps ב→'v',
# כ→'kh', ו→'v'. This is correct for native Hebrew names but WRONG for
# Arab-society victim names written in Hebrew script — those use Hebrew
# letters to represent Arabic sounds: ב=ب=b, כ=ك=k, ו=و=w.
#
# Result before this map: 'בכר מחמוד יאסין' romanized as 'vkhr mhmvd ysyn'
#                vs Arabic 'بكر محمود ياسين' as 'bkr mhmwd ysyn'
# Jaro = 0.830 (below the verify 0.85 threshold → silent recall miss).
#
# After this map: Hebrew rom = 'bkr mhmwd ysyn', identical to Arabic.
# Jaro = 1.000.
#
# Trade-off: native Hebrew names with these letters also get biased toward
# Arabic phonetics, but same-script self-comparisons stay unaffected because
# both sides go through the same transformation.
_HEBREW_ARABIC_BIAS_MAP = str.maketrans({
    "ב": "b",   # bet (vs anyascii's 'v')
    "כ": "k",   # kaf (vs anyascii's 'kh')
    "ך": "k",   # kaf sofit
    "ו": "w",   # vav as waw (vs anyascii's 'v')
})


def romanize_name(name: str) -> str:
    """
    Convert Arabic/Hebrew name to ASCII romanization for Jaro-Winkler comparison.
    Uses anyascii for transliteration + normalization.

    Hebrew letters that frequently appear in Arabic-origin names are
    pre-mapped to their Arabic-equivalent Latin (b, k, w) before anyascii
    runs so cross-script comparisons of the same victim land on the same
    romanized form.
    """
    name = normalize_arabic(name)
    name = strip_honorifics(name)
    name = name.translate(_HEBREW_ARABIC_BIAS_MAP)
    name = anyascii(name)
    name = re.sub(r"[^a-zA-Z\s]", "", name).lower().strip()
    name = re.sub(r"\s+", " ", name)
    return name


def jaro_winkler_similarity(name_a: str | None, name_b: str | None) -> float:
    """Compute Jaro-Winkler similarity on romanized names. Returns 0.0 if either is None."""
    if not name_a or not name_b:
        return 0.0
    import jellyfish

    rom_a = romanize_name(name_a)
    rom_b = romanize_name(name_b)
    if not rom_a or not rom_b:
        return 0.0
    return jellyfish.jaro_winkler_similarity(rom_a, rom_b)
