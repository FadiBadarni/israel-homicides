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

  const evidence = c.media_evidence ?? [];
  const hasPhotos = evidence.length > 0;
  const attributionFor = (item: typeof evidence[number]): string => {
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
          <img src="/logo.png" alt="" className="breadcrumb-mark" aria-hidden="true" />
          <span>{t(lang, "case.breadcrumb")}</span>
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

      {hasPhotos && (
        <div className={`case-photos count-${Math.min(evidence.length, 3)}`}>
          {evidence.map((m, i) => {
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
      )}

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
