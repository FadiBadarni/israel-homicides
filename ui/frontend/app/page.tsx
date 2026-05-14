"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import { fetchMemorial, type Locality, type MemorialResponse, type DeathSummary } from "@/lib/api";
import { regionFor, regionLabel, type RegionKey } from "@/lib/regions";
import { formatDate, yearOf } from "@/lib/format";
import { useLanguage } from "@/lib/language-context";
import { t, pickLangField, pickNameWithTransliteration } from "@/lib/i18n";
import { LanguageToggle } from "@/components/language-toggle";

interface DeathWithCity extends DeathSummary {
  city_en: string;
  city_ar: string | null;
  city_he: string | null;
  region: RegionKey | null;
}

function flattenDeaths(localities: Locality[]): DeathWithCity[] {
  return localities.flatMap((loc) =>
    loc.deaths.map((d) => ({
      ...d,
      city_en: loc.city,
      city_ar: loc.city_ar,
      city_he: loc.city_he,
      region: regionFor(loc.city),
    }))
  );
}

const CASES_PER_PAGE = 9;

export default function HomePage() {
  const { lang } = useLanguage();
  const [memorial, setMemorial] = useState<MemorialResponse | null>(null);
  const [activeFilter, setActiveFilter] = useState<RegionKey | "all" | "current-year">("all");
  const [casesPage, setCasesPage] = useState(0);
  // The "featured year" stat is meant to be the most recent COMPLETED year,
  // shown next to the in-progress current year for an at-a-glance comparison.
  // Was hardcoded to 2026; that duplicated the current-year stat once 2026
  // arrived. Deriving from `new Date()` keeps the two stats distinct.
  const featuredYear = new Date().getFullYear() - 1;

  useEffect(() => {
    fetchMemorial()
      .then(setMemorial)
      .catch(() => setMemorial({
        run_id: null,
        year_range: { from: null, to: null },
        total_deaths: 0,
        documented_deaths: 0,
        under_40_pct: 0,
        unresolved_count: 0,
        year_counts: {},
        localities: [],
      }));
  }, []);

  const allDeaths = useMemo(
    () => (memorial ? flattenDeaths(memorial.localities) : []),
    [memorial]
  );

  const currentYear = new Date().getFullYear();
  const totalAll = memorial?.documented_deaths ?? memorial?.total_deaths ?? 0;
  const currentYearCount =
    memorial?.year_counts[String(currentYear)] ??
    allDeaths.filter((d) => yearOf(d.incident_date) === currentYear).length;
  const featuredYearCount =
    memorial?.year_counts[String(featuredYear)] ??
    allDeaths.filter((d) => yearOf(d.incident_date) === featuredYear).length;
  const ageData = allDeaths.filter((d) => d.victim_age !== null);
  const under40Pct =
    memorial?.under_40_pct ??
    (ageData.length === 0
      ? 0
      : Math.round((ageData.filter((d) => (d.victim_age ?? 0) < 40).length / ageData.length) * 100));

  const regionCounts = useMemo(() => {
    const c: Record<RegionKey, number> = { galilee: 0, triangle: 0, negev: 0, mixed: 0 };
    for (const d of allDeaths) {
      if (d.region) c[d.region] += 1;
    }
    return c;
  }, [allDeaths]);
  const maxRegion = Math.max(1, ...Object.values(regionCounts));

  const filteredCases = useMemo(() => {
    let filtered = allDeaths;
    if (activeFilter === "current-year") {
      filtered = allDeaths.filter((d) => yearOf(d.incident_date) === currentYear);
    } else if (activeFilter !== "all") {
      filtered = allDeaths.filter((d) => d.region === activeFilter);
    }
    return filtered
      .slice()
      .sort((a, b) => (b.incident_date ?? "").localeCompare(a.incident_date ?? ""));
  }, [allDeaths, activeFilter, currentYear]);

  const totalPages = Math.max(1, Math.ceil(filteredCases.length / CASES_PER_PAGE));
  const currentPage = Math.min(casesPage, totalPages - 1);
  const recentCases = useMemo(
    () => filteredCases.slice(currentPage * CASES_PER_PAGE, (currentPage + 1) * CASES_PER_PAGE),
    [filteredCases, currentPage]
  );

  useEffect(() => {
    setCasesPage(0);
  }, [activeFilter]);

  const yearlyData = useMemo(() => {
    // Prefer the API's ``year_counts`` (computed from ALL documented_deaths
    // including gazetteer-unresolved cities) so the tapestry matches the
    // hero count. Fall back to flattened-localities-derived counts only if
    // the API didn't return year_counts for some reason.
    const yc = memorial?.year_counts;
    if (yc && Object.keys(yc).length) {
      return Object.entries(yc)
        .map(([y, n]) => ({ year: Number(y), n: n as number, current: Number(y) === currentYear }))
        .sort((a, b) => a.year - b.year);
    }
    const byYear = new Map<number, number>();
    for (const d of allDeaths) {
      const y = yearOf(d.incident_date);
      if (y === null) continue;
      byYear.set(y, (byYear.get(y) ?? 0) + 1);
    }
    return Array.from(byYear.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([year, n]) => ({ year, n, current: year === currentYear }));
  }, [memorial, allDeaths, currentYear]);

  // No early return — render the page immediately so the static nav + hero +
  // footer appear on first paint. Data sections start at opacity 0 (their
  // layout space is reserved) and ease in once the memorial fetch resolves.
  const dataReady = !!memorial;

  return (
    <>
      <nav className="top">
        <div className="wrap row">
          <Link href="/" className="brand">
            <img src="/logo.png" alt="" className="brand-mark" aria-hidden="true" />
            <span>{t(lang, "brand")}</span>
          </Link>
          <div className="links" style={{ alignItems: "center" }}>
            <a href="#cases">{t(lang, "nav.cases")}</a>
            <a href="#regions">{t(lang, "nav.regions")}</a>
            <a href="#years">{t(lang, "nav.years")}</a>
            <a href="#about">{t(lang, "nav.about")}</a>
            <LanguageToggle />
          </div>
        </div>
      </nav>

      <header className="hero">
        <div className="wrap">
          <div className="eyebrow">{t(lang, "hero.eyebrow")}</div>
          <h1>{t(lang, "hero.h1").split("\n").map((line, i, arr) => (
            <span key={i}>
              {line}
              {i < arr.length - 1 && <br />}
            </span>
          ))}</h1>
          <p className="lede">{t(lang, "hero.lede")}</p>
        </div>
      </header>

      <section className={`stats wrap fade-in ${dataReady ? "ready" : ""}`}>
        {/* Stat 1 — Scale. The total in the register since documentation began.
            Gets the dominant 1.4fr column + 128px font via .stat:first-child. */}
        <div className="stat">
          <div className="num">{totalAll}</div>
          <div className="label">{t(lang, "stats.total")}</div>
        </div>
        {/* Stat 2 — Urgency. Current year so far, in blood-red. */}
        <div className="stat">
          <div className="num" style={{ color: "var(--blood)" }}>{currentYearCount}</div>
          <div className="label">
            {t(lang, "stats.current_year_prefix")} {currentYear} {t(lang, "stats.current_year_suffix")}
          </div>
        </div>
        {/* Stat 3 — Context. Most recent completed year. */}
        <div className="stat">
          <div className="num">{featuredYearCount || "—"}</div>
          <div className="label">
            {t(lang, "stats.last_year", { year: featuredYear })}
          </div>
        </div>
        {/* Stat 4 — Demographic. Share of victims under 40. */}
        <div className="stat">
          <div className="num">{under40Pct}%</div>
          <div className="label">{t(lang, "stats.under_40_pct")}</div>
        </div>
      </section>

      <section className={`sec fade-in delay-1 ${dataReady ? "ready" : ""}`} id="cases">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">{t(lang, "sec.cases_title")}</h2>
            <div className="sec-meta">{t(lang, "sec.cases_meta")}</div>
          </div>

          <div className="filters">
            <button
              className={`filter ${activeFilter === "all" ? "on" : ""}`}
              onClick={() => setActiveFilter("all")}
            >
              {t(lang, "filter.all")} <span className="count">{totalAll}</span>
            </button>
            <button
              className={`filter ${activeFilter === "current-year" ? "on" : ""}`}
              onClick={() => setActiveFilter("current-year")}
            >
              {currentYear} <span className="count">{currentYearCount}</span>
            </button>
            {(["galilee", "triangle", "negev", "mixed"] as RegionKey[]).map((r) => (
              <button
                key={r}
                className={`filter ${activeFilter === r ? "on" : ""}`}
                onClick={() => setActiveFilter(r)}
              >
                {regionLabel(r, lang)} <span className="count">{regionCounts[r]}</span>
              </button>
            ))}
          </div>

          <div className="cases">
            {recentCases.map((d) => {
              const nameField = pickNameWithTransliteration(
                d.victim_name_ar,
                d.victim_name_he,
                d.victim_name_en,
                d.name_transliterations,
                lang,
              );
              const name = nameField.value;
              const city = pickLangField(d.city_ar, d.city_he, lang);
              return (
                <Link
                  key={`${d.run_id}-${d.case_index}`}
                  href={`/cases/${d.run_id}/${d.case_index}`}
                  className="case"
                >
                  <div className="date">{formatDate(d.incident_date, lang)}</div>
                  <div className={`name ${name === "—" ? "missing" : ""}`}>
                    {name}
                  </div>
                  <div className="meta">
                    {d.victim_age !== null && <span>{d.victim_age} {t(lang, "case.years_old")}</span>}
                    {d.victim_age !== null && <span className="sep">·</span>}
                    <span className={city === "—" ? "missing" : ""}>{city}</span>
                  </div>
                  <div className="footer">
                    <span className="badge">{t(lang, "badge.documenting")}</span>
                    <span className="arrow">←</span>
                  </div>
                </Link>
              );
            })}
            {recentCases.length === 0 && (
              <div style={{ gridColumn: "1 / -1", padding: "48px 24px", textAlign: "center", color: "var(--muted)" }}>
                {t(lang, "no_cases")}
              </div>
            )}
          </div>

          {totalPages > 1 && (
            <div className="pagination">
              <button
                className="filter"
                onClick={() => setCasesPage((p) => Math.max(0, p - 1))}
                disabled={currentPage === 0}
              >
                {t(lang, "pagination.prev")}
              </button>
              <span className="pagination-status">
                {t(lang, "pagination.page_of", { page: currentPage + 1, total: totalPages })}
              </span>
              <button
                className="filter"
                onClick={() => setCasesPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={currentPage >= totalPages - 1}
              >
                {t(lang, "pagination.next")}
              </button>
            </div>
          )}
        </div>
      </section>

      <section className={`sec fade-in delay-2 ${dataReady ? "ready" : ""}`} id="regions">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">{t(lang, "sec.regions_title")}</h2>
            <div className="sec-meta">{t(lang, "sec.regions_meta")}</div>
          </div>

          <div className="regions">
            {(["galilee", "triangle", "negev", "mixed"] as RegionKey[]).map((r) => (
              <a
                key={r}
                href={`#${r}`}
                className="region"
                onClick={(e) => {
                  e.preventDefault();
                  setActiveFilter(r);
                  document.getElementById("cases")?.scrollIntoView({ behavior: "smooth" });
                }}
              >
                <div className="name">{regionLabel(r, lang)}</div>
                <div className="num">
                  <strong>{regionCounts[r]}</strong>
                  <span className="since">{t(lang, "victim_word")}</span>
                </div>
                <div className="bar">
                  <div className="fl" style={{ width: `${(regionCounts[r] / maxRegion) * 100}%` }} />
                </div>
              </a>
            ))}
          </div>
        </div>
      </section>

      <section className={`sec fade-in delay-3 ${dataReady ? "ready" : ""}`} id="years">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">{t(lang, "sec.years_title")}</h2>
            <div className="sec-meta">
              {t(lang, "sec.years_meta_prefix")}
              <span className="tap-legend" style={{ display: "inline-flex" }}>
                <span className="swatch" />
              </span>
              {t(lang, "sec.years_meta_suffix")}
            </div>
          </div>

          <div className="tapestry">
            {yearlyData.map((d) => (
              <div
                key={d.year}
                className={`tap-row ${d.current ? "current" : ""} ${d.year < currentYear - 2 ? "faded" : ""}`}
              >
                <div className="tap-label">
                  <div className="tap-year">
                    {d.year}
                    {d.current && (
                      <span style={{ fontSize: 12, color: "var(--blood)", marginRight: 6, letterSpacing: ".04em" }}>
                        {t(lang, "current_label")}
                      </span>
                    )}
                  </div>
                  <div className="tap-count">
                    <strong>{d.n}</strong> {t(lang, "victim_word")}
                  </div>
                </div>
                <div className="tap-dots">
                  {Array.from({ length: d.n }).map((_, i) => (
                    <span key={i} className="dot-cell" />
                  ))}
                </div>
              </div>
            ))}
            {yearlyData.length === 0 && (
              <p style={{ color: "var(--muted)" }}>{t(lang, "no_yearly")}</p>
            )}
          </div>

          {yearlyData.length > 0 && (
            <p className="tap-caption">{t(lang, "tap_caption")}</p>
          )}
        </div>
      </section>

      <section className="sec" id="about">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">{t(lang, "sec.about_title")}</h2>
          </div>

          <div className="about-grid">
            <div className="about-col">
              <h3>{t(lang, "about.col1.h")}</h3>
              <p>{t(lang, "about.col1.p")}</p>
            </div>
            <div className="about-col">
              <h3>{t(lang, "about.col2.h")}</h3>
              <p>{t(lang, "about.col2.p")}</p>
            </div>
            <div className="about-col">
              <h3>{t(lang, "about.col3.h")}</h3>
              <p>{t(lang, "about.col3.p")}</p>
            </div>
          </div>
        </div>
      </section>

      <footer className="bottom">
        <div className="wrap">
          <p>{t(lang, "footer.line1")}</p>
        </div>
      </footer>
    </>
  );
}
