"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { fetchCase, type CaseDetail } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useLanguage } from "@/lib/language-context";
import { t, pickNameWithTransliteration, MISSING } from "@/lib/i18n";
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
          <Link href="/">{t(lang, "case.breadcrumb")}</Link>
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
  const cityLabel = lang === "ar" ? c.city : c.city; // city is single-lang on CaseDetail; keep as-is

  return (
    <div className="case-page">
      <div className="breadcrumb" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Link href="/">{t(lang, "case.breadcrumb")}</Link>
        <LanguageToggle />
      </div>

      <header className="masthead">
        <div className="age-label">{t(lang, "case.in_memory")}</div>
        <h1 className={`case-name ${name === MISSING ? "missing" : ""}`}>
          {name}
          {!nameField.isAttested && !nameField.isMissing && (
            <span
              className="inferred-badge"
              title={
                `Transliterated from ${nameField.sourceScript} ` +
                `(method: ${nameField.method})`
              }
              style={{
                fontSize: "0.45em",
                marginInlineStart: "0.6em",
                padding: "0.2em 0.5em",
                borderRadius: "0.4em",
                border: "1px solid var(--muted, #aaa)",
                color: "var(--muted, #888)",
                verticalAlign: "middle",
                fontWeight: 400,
                letterSpacing: "0.02em",
              }}
            >
              ⓘ inferred
            </span>
          )}
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

      {c.case_narrative && <p className="case-summary">{c.case_narrative}</p>}

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
            {c.district && <div className="sub">{c.district}</div>}
          </div>
          <div className="fact">
            <div className="k">{t(lang, "case.facts.cause")}</div>
            <div className="v">{c.weapon_type || MISSING}</div>
          </div>
          <div className="fact">
            <div className="k">{t(lang, "case.facts.suspect_status")}</div>
            <div className="v small">{c.suspect_status || MISSING}</div>
          </div>
        </div>
      </section>

      {c.sources.length > 0 && (
        <>
          <div className="rule" />
          <section>
            <div className="section-label">{t(lang, "case.sources_label")}</div>
            <div className="sources-list">
              {c.sources.map((s, i) => (
                <div className="source-item" key={i}>
                  <a href={s.url} target="_blank" rel="noopener noreferrer" className="source-pub">
                    {s.title || s.domain}
                  </a>
                  <span className="source-date">{s.published_at || ""}</span>
                </div>
              ))}
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
