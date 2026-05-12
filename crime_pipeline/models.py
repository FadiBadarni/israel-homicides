"""
SQLAlchemy ORM models and Pydantic v2 validation schemas for the crime pipeline.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# SQLAlchemy declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# ORM Tables
# ---------------------------------------------------------------------------


class RawArticle(Base):
    """Stores the raw fetched content of a news article before LLM extraction."""

    __tablename__ = "raw_articles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)
    final_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)  # "ar" | "he"
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    raw_html: Mapped[str] = mapped_column(Text, nullable=False)
    article_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="article")
    fetch_status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # The pipeline run that fetched this article. Lets resume-from-dedup
    # runs (`--stage dedup ...`) scope to the current run instead of seeing
    # every article ever fetched in the shared SQLite DB. Critical for the
    # multi-city backfill flow (--cities arraba,sakhnin,...) where 30 sequential
    # runs would otherwise cross-contaminate each other's dedup blocking.
    # Nullable so legacy rows (pre-this-column) still load.
    pipeline_run_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )

    # Triage stage metadata. Populated by the C-stage classifier between
    # fetch and extract. Used to skip full LLM extraction on articles whose
    # title+lede don't look like a homicide. Persisted so we can audit
    # rejections + replay on prompt changes without re-fetching.
    #   triage_status: "yes" | "maybe" | "no" | None (None = not yet triaged)
    #   triage_incident_type: the LLM's incident_type label (hint, not authoritative)
    #   triage_reason: short string for stats (e.g. "non_crime", "accident")
    #   triage_model_version: which model + prompt version produced the verdict
    triage_status: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)
    triage_incident_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    triage_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    triage_model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    triage_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    triage_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    extractions: Mapped[list["ExtractedRecord"]] = relationship(
        "ExtractedRecord", back_populates="article", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<RawArticle id={self.id!r} source={self.source!r} url={self.url!r}>"


class ExtractedRecord(Base):
    """Stores LLM extraction results for a single article."""

    __tablename__ = "extracted_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    article_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("raw_articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extracted_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False)  # valid | invalid
    llm_model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    extraction_status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")

    article: Mapped["RawArticle"] = relationship("RawArticle", back_populates="extractions")

    def __repr__(self) -> str:
        return (
            f"<ExtractedRecord id={self.id!r} article_id={self.article_id!r} "
            f"status={self.extraction_status!r}>"
        )


class CanonicalCase(Base):
    """
    A deduplicated, merged crime case assembled from one or more RawArticle extractions.
    """

    __tablename__ = "canonical_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    case_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sources_merged: Mapped[list[str]] = mapped_column(JSON, nullable=False)  # list of URLs
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    pipeline_run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<CanonicalCase id={self.id!r} review_status={self.review_status!r}>"


class CaseNarrative(Base):
    """LLM-generated 2-3 sentence memorial summary, cached per case.

    Composite primary key ``(canonical_case_id, sources_hash)`` because
    the merger can produce more than one canonical case sharing the same
    ``canonical_case_id`` (slug collision when two clusters about the
    same person merge differently in different runs). Pairing case_id
    with the source-URL hash makes the cache row unique by content.

    Invalidated when ``sources_hash`` changes (new merge / additive
    enrichment) or ``model_version`` changes (prompt/model bump).
    """

    __tablename__ = "case_narratives"

    canonical_case_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    sources_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    narrative_ar: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    narrative_he: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    narrative_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Pydantic v2 schemas for LLM output validation
# ---------------------------------------------------------------------------


class MediaItem(BaseModel):
    """A media artefact referenced in a news article (image, video, etc.)."""

    type: Literal[
        "victim_portrait", "scene", "police_evidence", "suspect_photo",
        "funeral", "memorial", "video", "infographic", "other"
    ]
    status: Literal["available", "blurred", "described_only", "unavailable"] = "available"
    caption: Optional[str] = None
    url: Optional[str] = None  # If extractable from article


class CanonicalMedia(BaseModel):
    """Canonical merged media record persisted in CanonicalCaseSchema.

    One CanonicalMedia represents ONE distinct image (after cross-source
    perceptual-hash dedup) that appeared in one or more articles about
    the case. Mirror URLs list the other publishers that hosted the same
    image.
    """

    media_id: str  # phash-based stable id
    type: Literal[
        "victim_portrait", "suspect_portrait", "crime_scene", "weapon",
        "court", "funeral", "cctv", "police_activity", "generic_stock",
        "infographic", "video", "other",
    ]
    status: Literal["available", "blurred", "described_only", "unavailable"] = "available"

    # URLs
    primary_url: str
    mirror_urls: list[str] = Field(default_factory=list)
    source_article_urls: list[str] = Field(default_factory=list)

    # Visual metadata
    caption: Optional[str] = None
    alt_text: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    mime_type: Optional[str] = None

    # Hashes (sha256 = byte-exact, phash = perceptual)
    sha256: Optional[str] = None
    phash: Optional[str] = None

    # Classification
    classifier_tier: Literal["keyword", "clip", "gemini", "manual"] = "keyword"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    classification_evidence: list[str] = Field(
        default_factory=list,
        description="Signals that supported the classification, e.g. "
                    "['caption_match:victim_name', 'clip:0.31', 'phash_match:5']",
    )

    # Stock vs evidentiary
    is_stock_photo: bool = False
    is_evidence: bool = False
    evidence_reason: Optional[str] = None
    appearance_count: int = 1  # how many distinct articles carried this image


class TransliteratedName(BaseModel):
    """A name form generated by the post-merge transliteration step,
    NOT attested in any source article.

    Stored separately from the source-of-truth ``victim_name_*`` fields
    so consumers (the React UI) can render attested names normally and
    inferred names with a "ⓘ inferred" badge. Never overwrites an
    attested value — only fills gaps.
    """

    value: str
    target_script: Literal["ar", "he", "en"]      # script we transliterated TO
    source_script: Literal["ar", "he", "en"]      # script we transliterated FROM
    method: Literal["rule_based", "dictionary"]
    # Optional source-of-truth value we transliterated from (for audit /
    # round-trip verification).
    source_value: Optional[str] = None


class EvidenceItem(BaseModel):
    """A piece of evidence mentioned in connection with the incident."""

    description: str
    location_found: Optional[str] = None  # e.g. "laundry basket", "vehicle trunk"
    type: Optional[Literal["weapon", "physical", "digital", "testimony", "other"]] = None


class AdditionalVictim(BaseModel):
    """A secondary named victim mentioned in a multi-victim article.

    Used for week-in-review summaries ("13 قتيلا منذ بدء العام"), triple
    murders ("ياسر حجيرات, كامل حجيرات, خالد غدير"), and Negev/Galilee
    region reports listing several recent homicides.

    Intentionally slim — only the fields needed for downstream identity
    resolution (dedup) and verify-against-truth matching. Suspect,
    evidence, motive, media, and provenance live on the parent
    ``ExtractedArticleData`` because they describe the article, not the
    individual victim. Cross-victim suspect/motive attribution is a
    future-work problem; for now we accept that secondary victims share
    the parent's incident metadata.
    """

    victim_name: Optional[str] = None
    victim_name_ar: Optional[str] = None
    victim_name_he: Optional[str] = None
    victim_name_en: Optional[str] = None
    victim_age: Optional[int] = Field(default=None, ge=0, le=120)
    victim_gender: Optional[Literal["M", "F", "unknown"]] = None
    city: Optional[str] = None
    incident_date: Optional[date] = None
    victim_outcome: Optional[Literal["died", "survived", "critical", "unknown"]] = None

    @field_validator("incident_date", mode="before")
    @classmethod
    def _coerce_partial_date(cls, v: Any) -> Any:
        """Same partial-date tolerance as the parent extraction — accept
        ``"2026-01-XX"`` or ``"????-??-??"`` as None rather than rejecting
        the whole record."""
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            if any(ch in s.upper() for ch in ("X", "?")):
                return None
        return v


class ExtractedArticleData(BaseModel):
    """Structured data extracted from a single crime news article by the LLM."""

    # What KIND of incident the article is about. Drives the relevance filter:
    # only "homicide" and "attempted_homicide" enter the dedup pipeline.
    # Optional for backward compat with extractions made before this field
    # existed; None means "legacy / pre-discriminator extraction".
    incident_type: Optional[
        Literal[
            "homicide",
            "attempted_homicide",
            "accident",
            "suicide",
            "historical",
            "other_crime",
            "non_crime",
            "unknown",
        ]
    ] = None

    # WHERE the incident occurred and WHO the victim is, in terms of the
    # dataset's scope. Replaces the post-hoc city/name blocklists that
    # tried (and failed) to filter foreign-news bleed-through. The LLM
    # is already reading the article — it has all the context to make
    # this call directly. The relevance filter drops everything except
    # ``israel_arab_society`` (and optionally ``unknown`` for review).
    #
    # Categories:
    #   • israel_arab_society — Arab citizens of Israel (the dataset target)
    #   • israel_jewish_society — Jewish Israeli victim; legitimate Israeli
    #     homicide but out of scope here.
    #   • israel_other — foreign-national victims killed inside Israel
    #     (tourists, foreign workers).
    #   • palestinian_territories — Gaza Strip / West Bank incidents.
    #   • abroad — anywhere outside Israel + Palestinian territories
    #     (Iran, US, EU, Russia, etc.).
    #   • unknown — article is ambiguous about geography.
    #
    # None for legacy extractions made before this field existed.
    incident_geography: Optional[
        Literal[
            "israel_arab_society",
            "israel_jewish_society",
            "israel_other",
            "palestinian_territories",
            "abroad",
            "unknown",
        ]
    ] = None

    # Multilingual victim names — capture all variants present in the article
    victim_name: Optional[str] = None  # Primary name as it appears in article
    victim_name_ar: Optional[str] = None  # Arabic spelling if present
    victim_name_he: Optional[str] = None  # Hebrew spelling if present
    victim_name_en: Optional[str] = None  # Latin/English spelling if present
    victim_aliases: list[str] = Field(default_factory=list)  # Other name variants

    # Additional named victims in the SAME article (multi-victim case).
    # Empty by default. When non-empty, the pipeline's _explode_multivictim
    # step (between extract and dedup) flattens this into N+1 records so
    # each victim gets its own canonical case.
    additional_victims: list[AdditionalVictim] = Field(default_factory=list)

    victim_age: Optional[int] = Field(default=None, ge=0, le=120)
    victim_gender: Optional[Literal["M", "F", "unknown"]] = None
    victim_profession: Optional[str] = None
    victim_residence: Optional[str] = None  # City/town of residence (may differ from incident)

    # Date precision
    death_date: Optional[date] = None  # When victim was pronounced dead
    incident_date: Optional[date] = None  # When the act occurred (may differ from death_date)
    incident_time: Optional[str] = None  # HH:MM 24-hour format

    @field_validator("death_date", "incident_date", mode="before")
    @classmethod
    def _coerce_partial_date(cls, v: Any) -> Any:
        """The LLM sometimes emits partial dates like ``"2026-01-XX"`` when
        the article says e.g. "last month" without a specific day.

        Pre-fix: Pydantic rejected the WHOLE extraction over the bad date,
        which silently lost real homicide records (e.g. the Bakr Yassin
        homicide where the LLM correctly extracted everything else but
        emitted ``"incident_date": "2026-01-XX"`` based on "last month").

        Coerce any string containing non-digit positional placeholders to
        None — we lose date precision but keep the rest of the case alive.
        """
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            # Detect placeholder characters in date positions
            # e.g. "2026-01-XX", "2026-XX-XX", "????-??-??", "2026-01-??"
            if any(ch in s.upper() for ch in ("X", "?")):
                return None
        return v

    # Granular location
    city: Optional[str] = None
    neighborhood: Optional[str] = None  # e.g. "וואדי אל-עין" / "Wadi al-Ein"
    exact_place_type: Optional[
        Literal["family_home", "apartment", "street", "vehicle", "commercial",
                "open_area", "school", "other", "unknown"]
    ] = None
    district: Optional[str] = None  # Administrative district: Northern, Central, etc.
    region: Optional[str] = None  # Geographic region: Galilee, Negev, Sharon, etc.
    hospital: Optional[str] = None  # Where victim was transported / pronounced dead

    # Incident
    weapon_type: Optional[
        Literal["firearm", "knife", "blunt", "explosive", "vehicle", "other", "unknown"]
    ] = None
    weapon_subtype: Optional[str] = None  # e.g. "handgun", "rifle", "automatic firearm"
    num_victims: int = Field(default=1, ge=1)

    @field_validator("num_victims", mode="before")
    @classmethod
    def _coerce_num_victims(cls, v: Any) -> int:
        """The LLM occasionally emits null or 0 for unspecified victim count.
        Coerce to the schema default (1) instead of rejecting the whole
        extraction. A homicide article with no explicit victim count
        overwhelmingly describes a single victim — this is a safe default.
        """
        if v is None or v == 0 or v == "" or v == "null":
            return 1
        return v

    # Suspect (richer) — three-axis status separation:
    # - suspect_status:               PHYSICAL state of the suspect
    # - legal_status:                 LEGAL proceedings state
    # - police_investigation_status:  CASE state from the police's POV
    suspect_name: Optional[str] = None
    suspect_age: Optional[int] = Field(default=None, ge=0, le=120)
    suspect_relation: Optional[str] = None  # e.g. "brother", "neighbor", "ex-partner"
    suspect_profession: Optional[str] = None
    suspect_status: Optional[
        Literal["unknown", "at_large", "wanted", "arrested", "released_on_bail", "in_custody"]
    ] = None
    legal_status: Optional[
        Literal["pre_indictment", "indicted", "on_trial", "convicted", "acquitted", "case_closed"]
    ] = None
    police_investigation_status: Optional[
        Literal["open", "suspect_identified", "completed", "indictment_filed", "closed"]
    ] = None
    arrest_location: Optional[str] = None  # Where the suspect was apprehended

    # Legacy field — kept for backward compat. Will be auto-mapped to one of
    # the three new fields above by the sanity_pass module.
    police_status: Optional[str] = None

    # Evidence + media inventory
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    media_items: list[MediaItem] = Field(default_factory=list)

    # Context
    motive: Optional[str] = None
    organized_crime: Optional[bool] = None
    family_dispute: Optional[bool] = None
    community_context: Optional[str] = None  # e.g. "3rd Arab-society murder of 2026"

    # Lethality — critical for filtering non-fatal incidents from the homicides pipeline.
    # "died"=victim confirmed dead, "survived"=victim survived (attempted homicide),
    # "critical"=victim in critical condition (outcome unknown at press time), "unknown"=not stated.
    victim_outcome: Optional[Literal["died", "survived", "critical", "unknown"]] = None

    # Article-level metadata (defaults so json-repaired outputs validate even
    # when Gemini omits these fields)
    source_language: Literal["ar", "he", "en"] = "he"
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    extraction_notes: Optional[str] = None

    # Coverage indicators
    body_extracted: bool = True  # False if only headline/lede was available
    paywalled: bool = False  # True if article was behind a paywall

    model_config = {"str_strip_whitespace": True}


class SourceRef(BaseModel):
    """Reference to a source article that contributed to a canonical case."""

    url: str
    discovery_source: str  # how we found this (e.g. "googlenews", "ynet_search")
    actual_publisher: Optional[str] = None  # the real outlet (e.g. "haaretz", "kan", "arab48")
    source_name: str  # legacy field — kept for backward compat (= actual_publisher when known)
    language: Literal["ar", "he"]
    published_at: Optional[datetime] = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    paywalled: bool = False
    body_extracted: bool = True


class CanonicalCaseSchema(BaseModel):
    """
    Fully merged, deduplicated crime case combining data from multiple source articles.
    Represents the output of the Merger stage (and post-enrichment passes).
    """

    canonical_case_id: Optional[str] = None  # e.g. "IL-HOMICIDE-2026-ARRABA-2026-01-04-BAKR-YASSIN"

    # Scope discriminator — propagated from extraction so downstream
    # filters (and the React UI) can show / hide cases by geography.
    # See ``ExtractedArticleData.incident_geography`` for the value
    # semantics. None = legacy / not yet extracted.
    incident_geography: Optional[
        Literal[
            "israel_arab_society",
            "israel_jewish_society",
            "israel_other",
            "palestinian_territories",
            "abroad",
            "unknown",
        ]
    ] = None

    # Multilingual victim identity
    victim_name: Optional[str] = None  # primary display name
    victim_name_ar: Optional[str] = None
    victim_name_he: Optional[str] = None
    victim_name_en: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    # Post-merge transliteration fills for missing language fields.
    # ``victim_name_*`` above stay reserved for source-attested values;
    # inferred forms land here with explicit provenance so the UI can
    # render them with a "ⓘ inferred" badge. Empty by default.
    name_transliterations: list[TransliteratedName] = Field(default_factory=list)
    victim_age: Optional[int] = Field(default=None, ge=0, le=120)
    victim_gender: Optional[Literal["M", "F", "unknown"]] = None
    victim_profession: Optional[str] = None
    victim_residence: Optional[str] = None

    # Date precision
    death_date: Optional[date] = None
    incident_date: Optional[date] = None
    incident_date_possible: Optional[date] = None  # If sources disagree
    incident_time: Optional[str] = None

    @field_validator(
        "death_date", "incident_date", "incident_date_possible", mode="before"
    )
    @classmethod
    def _coerce_partial_date(cls, v: Any) -> Any:
        """Same coercion as ExtractedArticleData — protects the round-trip
        through dict in the inline cleanup stages from a stale partial date
        in legacy data."""
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            if any(ch in s.upper() for ch in ("X", "?")):
                return None
        return v

    # Granular location
    city: Optional[str] = None
    # {name_ar, name_he, name_en, district, region, lat, lng}. lat/lng are
    # floats from the gazetteer; other fields are strings — hence Any.
    city_normalized: Optional[dict[str, Any]] = None
    neighborhood: Optional[str] = None
    exact_place_type: Optional[str] = None
    district: Optional[str] = None  # Administrative: Northern, Central, Haifa, etc.
    region: Optional[str] = None  # Geographic: Galilee, Negev, Sharon, etc.
    hospital: Optional[str] = None

    # Incident
    weapon_type: Optional[
        Literal["firearm", "knife", "blunt", "explosive", "vehicle", "other", "unknown"]
    ] = None
    weapon_subtype: Optional[str] = None
    num_victims: int = Field(default=1, ge=1)

    @field_validator("num_victims", mode="before")
    @classmethod
    def _coerce_num_victims(cls, v: Any) -> int:
        """The LLM occasionally emits null or 0 for unspecified victim count.
        Coerce to the schema default (1) instead of rejecting the whole
        extraction. A homicide article with no explicit victim count
        overwhelmingly describes a single victim — this is a safe default.
        """
        if v is None or v == 0 or v == "" or v == "null":
            return 1
        return v

    # Suspect (three-axis status separation)
    suspect_name: Optional[str] = None
    suspect_age: Optional[int] = None
    suspect_relation: Optional[str] = None
    suspect_profession: Optional[str] = None
    suspect_profession_conflict: list[str] = Field(default_factory=list)
    suspect_status: Optional[str] = None  # PHYSICAL state: arrested / in_custody / wanted / ...
    legal_status: Optional[str] = None  # LEGAL: indicted / on_trial / convicted / ...
    police_investigation_status: Optional[str] = None  # CASE: open / completed / ...
    arrest_location: Optional[str] = None

    # Legacy field kept for backward compat
    police_status: Optional[str] = None

    # Evidence + media (lists, accumulated across sources).
    # `media`           — decorative / contextual / stock images
    # `media_evidence`  — images that depict the actual case (victim photos,
    #                     real crime-scene shots, the actual courtroom, etc.)
    # Both default to empty list so on-disk JSON files remain backward-compat.
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    media: list[dict[str, Any]] = Field(default_factory=list)
    media_evidence: list[dict[str, Any]] = Field(default_factory=list)

    # Lethality — "died" | "survived" | "critical" | "unknown" | None
    # Populated by merger from per-source victim_outcome fields. Cases where
    # outcome resolves to "survived" are flagged "non_fatal" and excluded from export.
    victim_outcome: Optional[Literal["died", "survived", "critical", "unknown"]] = None

    # Context
    motive: Optional[str] = None
    organized_crime: Optional[bool] = None
    family_dispute: Optional[bool] = None
    community_context: Optional[str] = None

    # Provenance
    sources: list[SourceRef] = Field(default_factory=list)
    conflicts: dict[str, Any] = Field(
        default_factory=dict,
        description="Fields where sources disagreed; keys are field names, values are {source_url: value}",
    )
    flags: list[str] = Field(default_factory=list)

    # Multi-dimensional confidence per category — populated by sanity_pass.
    # Keeps confidence_score (rollup) for backward compat.
    confidence: dict[str, float] = Field(
        default_factory=dict,
        description="Per-category confidence: case_identity, victim_identity, timeline, "
                    "legal_status, location_detail, media",
    )
    confidence_score: float = Field(ge=0.0, le=1.0, default=0.0)

    # Cleanup-pass outputs. Populated by the inline sanity / quality / reconcile
    # stages between merge and export. Optional with sane defaults so legacy
    # output JSON files (written before these stages existed) still validate.
    tier_coverage: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-tier source publisher breakdown, e.g. "
                    "{'tier_1': ['ynet'], 'tier_2': ['arab48'], 'tier_3': [], 'untiered': []}",
    )
    timeline: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Chronological event list synthesized by sanity_pass.build_timeline. "
                    "Entries: {date, event, confidence, source_url?}",
    )
    motive_translations: Optional[list[str]] = Field(
        default=None,
        description="Translations of motive into other scripts; quality_pass omits when empty.",
    )
    arrest_location_translations: Optional[list[str]] = Field(default=None)
    dropped_invalid_sources: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Sources demoted by quality_pass (e.g. invalid Tier-3 paths).",
    )
    rejected_unrelated_articles: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Articles that quality_pass dropped as not actually about this case "
                    "(false-positive scrape hits). Kept for operator audit.",
    )
    reconciliation_provenance: list[dict[str, Any]] = Field(
        default_factory=list,
        description="If non-empty, this case absorbed others during the reconcile stage. "
                    "Each entry: {merged_from_url, reason, jaro_score}",
    )

    # Memorial summary (2-3 sentences) — generated by the narrate stage from
    # the merged structured fields + source article bodies. One variant per
    # display language. Cached in ``case_narratives`` keyed by canonical_case_id +
    # sources_hash so re-runs don't re-spend on unchanged source sets.
    case_narrative_ar: Optional[str] = None
    case_narrative_he: Optional[str] = None
    case_narrative_en: Optional[str] = None

    review_status: str = "auto"
    pipeline_run_id: str = ""
    enrichment_passes: int = 0  # How many enrichment loops have run on this case
