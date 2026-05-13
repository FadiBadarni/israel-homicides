"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { fetchCase, type CaseDetail } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useLanguage } from "@/lib/language-context";
import { t, pickNameWithTransliteration, pickCityLabel, pickNarrative, translateEnum, MISSING } from "@/lib/i18n";
import { LanguageToggle } from "@/components/language-toggle";

interface PageProps {
  params: Promise<{ runId: string; caseIndex: string }>;
}

export default function CaseDetailPage({ params }: PageProps) {
  const { runId, caseIndex } = use(params);
  const { lang } = useLanguage();
  const [c, setC] = useState<CaseDetail | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchCase(runId, Number(caseIndex))
      .then(setC)
      .catch(() => setError(true));
  }, [runId, caseIndex]);

  if (error) {
    return (
      <div className="case-page">
        <div className="breadcrumb">
          <Link href="/" className="breadcrumb-link">
            <span className="breadcrumb-arrow" aria-hidden="true">→</span>
            <img src="/logo.png" alt="" className="breadcrumb-mark" aria-hidden="true" />
            <span>{t(lang, "brand")}</span>
          </Link>
        </div>
        <p style={{ textAlign: "center", marginTop: 96, color: "var(--muted)" }}>
          {t(lang, "case.load_failed")}
        </p>
      </div>
    );
  }

  if (!c) return <div className="case-page" style={{ minHeight: "60vh" }} />;

  const nameField = pickNameWithTransliteration(
    c.victim_name_ar,
    c.victim_name_he,
    c.victim_name_en,
    c.name_transliterations,
    lang,
  );
  const name = nameField.value;
  // Use the gazetteer-normalized record for the user's chosen language;
  // fall back to the raw extracted ``city`` when the gazetteer doesn't
  // know the city or doesn't have the requested script.
  const cityLabel = pickCityLabel(c.city, c.city_normalized, lang);

  // Build the displayable photo set.
  //   1. Keep evidence items + decorative items of meaningful types
  //      (portrait / crime scene / weapon / police / suspect / courtroom).
  //      Skip ``type: "other"`` — sidebar promos and unrelated thumbnails.
  //   2. Drop items whose caption explicitly names a DIFFERENT victim.
  //      Roundup articles ("13 dead since New Year") carry photos for
  //      many victims; the type-classifier promotes them all even though
  //      the caption tells us they belong to someone else.
  //   3. Dedupe by primary_url. Sort: keyword tier > clip tier, then by
  //      confidence. Cap each type at 2 items so a single category
  //      doesn't flood the gallery (e.g. the same weapon syndicated
  //      across 3 publishers). Final cap at 8.
  // Memorial-appropriate photo categories. Includes the victim and the
  // case context (incident, weapon, police, legal proceedings, funeral)
  // but deliberately excludes ``suspect_portrait`` (presumption of
  // innocence — the accused isn't part of the register) and ``cctv``
  // (often shows the moment of death; not memorial-appropriate).
  const MEANINGFUL_TYPES = new Set([
    "victim_portrait",
    "crime_scene",
    "weapon",
    "police_activity",
    "funeral",
    "court",
    "courtroom",
  ]);
  // Arabic + Hebrew victim/deceased-marker words. Singular, plural,
  // dual, with and without the definite article. Loose by design — a
  // false positive here just means "filter must check a caption that
  // didn't actually identify a victim" (cheap), while a false negative
  // means a wrong-victim photo slips through.
  const VICTIM_MARKERS = /(المرحوم|مرحوم|المغدور|مغدور|الضحية|ضحية|الضحايا|ضحايا|ضحيتا|الشهيد|شهيد|الراحل|القتيل|الشاب|המנוח|הקרבן|הקרבנות)/;
  // Use SURNAME-only matching (last token of each victim name). Middle
  // names like "محمود" / "מחמוד" are too common across families and
  // would falsely match a different victim's photo.
  const caseVictimSurnames = (
    [c.victim_name_ar, c.victim_name_he, c.victim_name_en, c.victim_name, ...(c.aliases ?? [])]
      .filter((n): n is string => Boolean(n))
      .map((n) => n.trim().split(/\s+/).pop() ?? "")
      .filter((t) => t.length >= 3)
  );
  const captionNamesOtherVictim = (m: { alt_text?: string | null; caption?: string | null }): boolean => {
    const text = `${m.alt_text ?? ""} ${m.caption ?? ""}`.trim();
    if (!text || !VICTIM_MARKERS.test(text)) return false;
    return !caseVictimSurnames.some((s) => text.includes(s));
  };
  // City tokens for location-mismatch filtering. Includes the raw city
  // string plus normalized variants from the gazetteer (name_ar/he/en).
  const caseCityTokens = (
    [c.city, ...Object.values(c.city_normalized ?? {})]
      .filter((v): v is string => typeof v === "string" && v.length > 0)
      .flatMap((s) => s.split(/\s+/))
      .filter((t) => t.length >= 3)
  );
  // For location-typed photos (crime_scene / police_activity), if the
  // caption is non-empty, require this case's city to appear in it.
  // Catches captions like "من موقع الجريمة المُرتكبة في الناصرة" when
  // the case city is Arraba — those photos belong to other incidents
  // pulled in from multi-victim roundup articles.
  const captionMentionsOtherLocation = (m: {
    type?: string | null;
    alt_text?: string | null;
    caption?: string | null;
  }): boolean => {
    if (m.type !== "crime_scene" && m.type !== "police_activity") return false;
    const text = `${m.alt_text ?? ""} ${m.caption ?? ""}`.trim();
    if (!text) return false;
    return !caseCityTokens.some((tok) => text.includes(tok));
  };

  const allMedia = [...(c.media_evidence ?? []), ...(c.media ?? [])];
  const seenUrl = new Set<string>();
  const perTypeCount: Record<string, number> = {};
  const photos = allMedia
    .filter((m) => {
      if (!m.primary_url || seenUrl.has(m.primary_url)) return false;
      if (!m.is_evidence && !MEANINGFUL_TYPES.has(m.type ?? "")) return false;
      if (captionNamesOtherVictim(m)) return false;
      if (captionMentionsOtherLocation(m)) return false;
      seenUrl.add(m.primary_url);
      return true;
    })
    .sort((a, b) => {
      const ta = a.classifier_tier === "keyword" ? 0 : a.classifier_tier === "clip" ? 1 : 2;
      const tb = b.classifier_tier === "keyword" ? 0 : b.classifier_tier === "clip" ? 1 : 2;
      if (ta !== tb) return ta - tb;
      return (b.confidence ?? 0) - (a.confidence ?? 0);
    })
    .filter((m) => {
      const key = m.type ?? "other";
      perTypeCount[key] = (perTypeCount[key] ?? 0) + 1;
      return perTypeCount[key] <= 2;
    })
    .slice(0, 8);

  const hasPhotos = photos.length > 0;
  const attributionFor = (item: typeof photos[number]): string => {
    const url = item.source_article_urls?.[0];
    if (!url) return "";
    const match = c.sources.find((s) => s.url === url);
    if (match) {
      return (
        match.source_name ||
        match.actual_publisher ||
        match.domain ||
        ""
      );
    }
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch {
      return "";
    }
  };

  return (
    <div className="case-page">
      <div className="breadcrumb" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Link href="/" className="breadcrumb-link">
          <span className="breadcrumb-arrow" aria-hidden="true">→</span>
          <img src="/logo.png" alt="" className="breadcrumb-mark" aria-hidden="true" />
          <span>{t(lang, "brand")}</span>
        </Link>
        <LanguageToggle />
      </div>

      <header className="masthead">
        {!hasPhotos && (c.victim_gender === "M" || c.victim_gender === "F") && (
          <figure className="portrait" aria-hidden="true">
            <img
              src={c.victim_gender === "F" ? "/woman-placeholder.png" : "/man-placeholder.png"}
              alt=""
            />
          </figure>
        )}
        <div className="age-label">{t(lang, "case.in_memory")}</div>
        <h1 className={`case-name ${name === MISSING ? "missing" : ""}`}>
          {name}
        </h1>
        <div className="lifespan">
          {c.victim_age !== null && (
            <>
              <span>{c.victim_age} {t(lang, "case.years_old")}</span>
              <span className="lsep">·</span>
            </>
          )}
          {c.city && (
            <>
              <span>{t(lang, "case.from")} {cityLabel}</span>
              <span className="lsep">·</span>
            </>
          )}
          {c.incident_date && (
            <span>{t(lang, "case.killed_on")} {formatDate(c.incident_date, lang)}</span>
          )}
        </div>
      </header>

      {(() => {
        const narrative = pickNarrative(
          c.case_narrative_ar,
          c.case_narrative_he,
          c.case_narrative_en,
          c.case_narrative,
          lang,
        );
        return narrative ? <p className="case-summary">{narrative}</p> : null;
      })()}

      <div className="rule" />

      <section>
        <div className="section-label">{t(lang, "case.facts_label")}</div>
        <div className="facts">
          <div className="fact">
            <div className="k">{t(lang, "case.facts.date")}</div>
            <div className="v">{formatDate(c.incident_date, lang) || MISSING}</div>
          </div>
          <div className="fact">
            <div className="k">{t(lang, "case.facts.location")}</div>
            <div className="v">{cityLabel || MISSING}</div>
            {c.district && (
              <div className="sub">{translateEnum("district", c.district, lang)}</div>
            )}
          </div>
          <div className="fact">
            <div className="k">{t(lang, "case.facts.cause")}</div>
            <div className="v">{translateEnum("weapon_type", c.weapon_type, lang)}</div>
          </div>
          <div className="fact">
            <div className="k">{t(lang, "case.facts.suspect_status")}</div>
            <div className="v small">{translateEnum("suspect_status", c.suspect_status, lang)}</div>
          </div>
        </div>
      </section>

      {hasPhotos && (
        <>
          <div className="rule" />
          <section>
            <div className={`case-photos count-${Math.min(photos.length, 3)}`}>
              {photos.map((m, i) => {
                const credit = attributionFor(m);
                return (
                  <figure className="case-photo" key={i}>
                    <img
                      src={m.primary_url}
                      alt={m.alt_text || m.caption || name}
                      loading="lazy"
                    />
                    {(credit || m.caption) && (
                      <figcaption>
                        {m.caption && <span className="cap">{m.caption}</span>}
                        {credit && <span className="credit">{credit}</span>}
                      </figcaption>
                    )}
                  </figure>
                );
              })}
            </div>
          </section>
        </>
      )}

      {c.sources.length > 0 && (
        <>
          <div className="rule" />
          <section>
            <div className="section-label">{t(lang, "case.sources_label")}</div>
            <div className="sources-list">
              {c.sources.map((s, i) => {
                const label =
                  s.title ||
                  s.source_name ||
                  s.actual_publisher ||
                  s.domain ||
                  (() => {
                    try {
                      return new URL(s.url).hostname.replace(/^www\./, "");
                    } catch {
                      return s.url;
                    }
                  })();
                return (
                  <div className="source-item" key={i}>
                    <a href={s.url} target="_blank" rel="noopener noreferrer" className="source-pub">
                      {label}
                    </a>
                    <span className="source-date">{formatDate(s.published_at, lang)}</span>
                  </div>
                );
              })}
            </div>
          </section>
        </>
      )}

      <footer className="case-footer">
        <p>{t(lang, "case.footer1")}</p>
        <p>{t(lang, "case.footer2")}</p>
      </footer>
    </div>
  );
}
