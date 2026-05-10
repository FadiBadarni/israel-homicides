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


def romanize_name(name: str) -> str:
    """
    Convert Arabic/Hebrew name to ASCII romanization for Jaro-Winkler comparison.
    Uses anyascii for transliteration + normalization.
    """
    name = normalize_arabic(name)
    name = strip_honorifics(name)
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
