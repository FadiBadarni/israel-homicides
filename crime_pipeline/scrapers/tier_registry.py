"""
Layered source registry: classifies every news domain into one of three tiers.

Tier 1 — Mainstream news (highest reliability).
    Best for: confirmed deaths, police statements, arrest status, indictments,
    hospital confirmation. Hebrew + English.

Tier 2 — Arabic / local press (highest richness).
    Best for: victim full names, family relations, neighborhood, funeral
    details, local context, photos / video.

Tier 3 — Official / public entities (highest legal accuracy).
    Best for: indictment status, police confirmation, legal proceedings.

The pipeline uses tier labels for:
  - Tier-aware confidence weighting per field (legal_status from a Tier 3
    source outranks Tier 1, which outranks Tier 2).
  - Coverage flags (needs_tier_2, needs_tier_3) when a tier is missing.
  - Tier-targeted enrichment queries that explicitly hunt for the missing
    tier (Tier 3 → site:police.gov.il queries; Tier 2 → Arabic-locale
    Google News with Arab48/Panet preference).
"""
from __future__ import annotations

from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Domain → tier mapping
# ---------------------------------------------------------------------------

# Tier 1: Mainstream Hebrew + English Israeli news
TIER_1_DOMAINS = {
    "ynet.co.il": "Ynet",
    "mako.co.il": "Mako",
    "n12.co.il": "Channel 12 / N12",
    "channel12.co.il": "Channel 12",
    "13tv.co.il": "Channel 13",
    "reshet.tv": "Channel 13",
    "haaretz.co.il": "Haaretz",
    "haaretz.com": "Haaretz English",
    "walla.co.il": "Walla",
    "news.walla.co.il": "Walla News",
    "israelhayom.co.il": "Israel Hayom",
    "kan.org.il": "Kan",
    "timesofisrael.com": "Times of Israel",
    "jpost.com": "Jerusalem Post",
    "calcalist.co.il": "Calcalist",
    "globes.co.il": "Globes",
    "maariv.co.il": "Maariv",
    "inn.co.il": "Arutz Sheva",
    "ynetnews.com": "Ynetnews",
    "emess.co.il": "EMess",
}

# Tier 2: Arabic / local press — richest for victim detail, neighborhood, family
TIER_2_DOMAINS = {
    "arab48.com": "Arab48",
    "makan.org.il": "Makan",
    "panet.com": "Panet",
    "kul-alarab.com": "Kul al-Arab",
    "bokra.net": "Bokra",
    "alarab.com": "Al-Arab",
    "alaraby.co.uk": "Al-Araby",
    "shfanews.com": "Shfa News",
    "shams.fm": "Shams FM",
    "raya.ps": "Raya",
}

# Tier 3: Official / government / legal
TIER_3_DOMAINS = {
    "police.gov.il": "Israel Police",
    "knesset.gov.il": "Knesset",
    "court.gov.il": "Israeli Courts",
    "justice.gov.il": "Ministry of Justice",
    "gov.il": "Government of Israel",
    "cbs.gov.il": "Central Bureau of Statistics",
}

DOMAIN_TO_TIER: dict[str, int] = {}
DOMAIN_TO_PUBLISHER: dict[str, str] = {}
for d, name in TIER_1_DOMAINS.items():
    DOMAIN_TO_TIER[d] = 1
    DOMAIN_TO_PUBLISHER[d] = name
for d, name in TIER_2_DOMAINS.items():
    DOMAIN_TO_TIER[d] = 2
    DOMAIN_TO_PUBLISHER[d] = name
for d, name in TIER_3_DOMAINS.items():
    DOMAIN_TO_TIER[d] = 3
    DOMAIN_TO_PUBLISHER[d] = name


# ---------------------------------------------------------------------------
# Tier metadata
# ---------------------------------------------------------------------------

TIER_METADATA = {
    1: {
        "name": "mainstream",
        "description": "Mainstream Israeli news (Hebrew/English). Reliable on confirmed facts.",
        "best_for": [
            "confirmed_death", "arrest", "indictment_announcement",
            "official_statements", "investigation_progress",
        ],
    },
    2: {
        "name": "arabic_local",
        "description": "Arabic / local press. Richest detail on victim and community.",
        "best_for": [
            "victim_name_ar", "victim_aliases", "neighborhood",
            "family_relations", "funeral_details", "media", "community_context",
        ],
    },
    3: {
        "name": "official",
        "description": "Official government / legal sources. Highest legal authority.",
        "best_for": [
            "legal_status", "police_investigation_status",
            "indictment_text", "court_case_id", "official_press_release",
        ],
    },
}

# Per-field tier preference. Higher-priority tier wins on conflict.
# Format: field_name -> list of tiers in priority order.
FIELD_TIER_PREFERENCE: dict[str, list[int]] = {
    # Legal axis: official > mainstream > local
    "legal_status": [3, 1, 2],
    "police_investigation_status": [3, 1, 2],
    "suspect_status": [3, 1, 2],
    "court_case_id": [3, 1, 2],

    # Confirmed facts: mainstream + official > local
    "incident_date": [1, 3, 2],
    "death_date": [1, 3, 2],
    "victim_age": [1, 2, 3],
    "victim_gender": [1, 2, 3],
    "weapon_type": [1, 3, 2],
    "weapon_subtype": [1, 2, 3],
    "num_victims": [1, 3, 2],

    # Local detail: Arabic/local > mainstream > official
    "victim_name_ar": [2, 1, 3],
    "victim_aliases": [2, 1, 3],
    "victim_profession": [2, 1, 3],
    "victim_residence": [2, 1, 3],
    "neighborhood": [2, 1, 3],
    "exact_place_type": [2, 1, 3],
    "community_context": [2, 1, 3],
    "media_items": [2, 1, 3],

    # Hospital: mainstream first (formal reporting), local second
    "hospital": [1, 2, 3],

    # Suspect detail: mixed — mainstream usually leaks first, local fills detail
    "suspect_name": [1, 2, 3],
    "suspect_relation": [2, 1, 3],
    "suspect_profession": [2, 1, 3],
    "arrest_location": [1, 2, 3],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_valid_tier3_path(url: str) -> bool:
    """
    For Tier 3 (official) sources we ONLY accept URLs that look like real
    press releases / case documents. Generic forms, rights guides, FAQs, and
    static PDFs are not actual case material — they pollute the case record.
    """
    if not url:
        return False
    u = url.lower()
    # Police press release endpoints
    valid_substrings = (
        "/pressreleases/", "/press-releases/", "/press/", "/news/",
        "/announcements/", "/cases/", "/indictment", "/court_decisions/",
        "/decisions/", "/judgments/",
    )
    # Known junk paths
    junk_substrings = (
        "rights%20guide", "rights-guide", "rightsguide",
        "/forms/", "/faq", "/about", "/contact",
        "/zchuyot", "/zchooyot",  # rights guide (Hebrew)
    )
    if any(j in u for j in junk_substrings):
        return False
    return any(v in u for v in valid_substrings)


def classify_url(url: str) -> tuple[int | None, str | None]:
    """
    Classify a URL into (tier, publisher_display_name).

    For Tier 3 (gov/official), the URL must also pass is_valid_tier3_path() —
    otherwise we return (None, publisher) so the caller treats it as
    untiered/discardable rather than authoritative.

    Returns (None, None) for unknown domains.
    """
    if not url:
        return None, None
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return None, None

    def _resolve(tier: int, publisher: str) -> tuple[int | None, str]:
        # Tier 3 URLs must look like real press releases. Generic gov forms
        # are demoted to "untiered (None)" so the case record doesn't treat
        # a rights-guide PDF as authoritative legal evidence.
        if tier == 3 and not is_valid_tier3_path(url):
            return None, publisher
        return tier, publisher

    if host in DOMAIN_TO_TIER:
        return _resolve(DOMAIN_TO_TIER[host], DOMAIN_TO_PUBLISHER[host])
    for d, tier in DOMAIN_TO_TIER.items():
        if host.endswith("." + d) or host == d:
            return _resolve(tier, DOMAIN_TO_PUBLISHER[d])
    return None, None


def best_tier_for_field(field: str) -> int:
    """Return the most authoritative tier for a given field, or 1 by default."""
    pref = FIELD_TIER_PREFERENCE.get(field)
    if pref:
        return pref[0]
    return 1


def tier_priority_score(tier: int | None, field: str) -> int:
    """
    Return a numeric priority score for a (tier, field) pair, higher = better.
    Used for ranked field-merge resolution.
    Untiered (None) → 0.
    """
    if tier is None:
        return 0
    pref = FIELD_TIER_PREFERENCE.get(field) or [1, 2, 3]
    if tier in pref:
        # First in pref → highest score
        return 10 - pref.index(tier)
    return 1


def coverage_gaps(case: dict) -> list[str]:
    """
    Return tier-coverage gap flags for a case based on which tiers are present
    in its sources list. Returns flag strings like "needs_tier_2".
    """
    sources = case.get("sources") or []
    tiers_present = {s.get("tier") for s in sources if s.get("tier")}
    gaps: list[str] = []
    for t in (1, 2, 3):
        if t not in tiers_present:
            gaps.append(f"needs_tier_{t}")
    return gaps


def domains_for_tier(tier: int) -> list[str]:
    """List of canonical domains in a tier — used to build site: queries."""
    return [d for d, t in DOMAIN_TO_TIER.items() if t == tier]
