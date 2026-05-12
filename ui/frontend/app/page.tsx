"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import { fetchMemorial, type Locality, type MemorialResponse, type DeathSummary } from "@/lib/api";
import { regionFor, REGION_LABELS_AR, type RegionKey } from "@/lib/regions";
import { formatArabicDate, yearOf } from "@/lib/format";

interface DeathWithCity extends DeathSummary {
  city: string;
  city_ar: string | null;
  region: RegionKey | null;
}

function flattenDeaths(localities: Locality[]): DeathWithCity[] {
  return localities.flatMap((loc) =>
    loc.deaths.map((d) => ({
      ...d,
      city: loc.city,
      city_ar: loc.city_ar,
      region: regionFor(loc.city),
    }))
  );
}

export default function HomePage() {
  const [memorial, setMemorial] = useState<MemorialResponse | null>(null);
  const [activeFilter, setActiveFilter] = useState<RegionKey | "all" | "current-year">("all");

  useEffect(() => {
    fetchMemorial()
      .then(setMemorial)
      .catch(() => setMemorial({
        run_id: null,
        year_range: { from: null, to: null },
        total_deaths: 0,
        unresolved_count: 0,
        localities: [],
      }));
  }, []);

  const allDeaths = useMemo(
    () => (memorial ? flattenDeaths(memorial.localities) : []),
    [memorial]
  );

  const currentYear = new Date().getFullYear();

  // Stats
  const totalAll = memorial?.total_deaths ?? 0;
  const currentYearCount = allDeaths.filter((d) => yearOf(d.incident_date) === currentYear).length;
  const lastYearCount = allDeaths.filter((d) => yearOf(d.incident_date) === currentYear - 1).length;
  const ageData = allDeaths.filter((d) => d.victim_age !== null);
  const under40Pct = ageData.length === 0
    ? 0
    : Math.round((ageData.filter((d) => (d.victim_age ?? 0) < 40).length / ageData.length) * 100);

  // Region counts
  const regionCounts = useMemo(() => {
    const c: Record<RegionKey, number> = { galilee: 0, triangle: 0, negev: 0, mixed: 0 };
    for (const d of allDeaths) {
      if (d.region) c[d.region] += 1;
    }
    return c;
  }, [allDeaths]);
  const maxRegion = Math.max(1, ...Object.values(regionCounts));

  // Filtered + sorted recent cases (top 9)
  const recentCases = useMemo(() => {
    let filtered = allDeaths;
    if (activeFilter === "current-year") {
      filtered = allDeaths.filter((d) => yearOf(d.incident_date) === currentYear);
    } else if (activeFilter !== "all") {
      filtered = allDeaths.filter((d) => d.region === activeFilter);
    }
    return filtered
      .slice()
      .sort((a, b) => (b.incident_date ?? "").localeCompare(a.incident_date ?? ""))
      .slice(0, 9);
  }, [allDeaths, activeFilter, currentYear]);

  // Tapestry: per-year counts
  const yearlyData = useMemo(() => {
    const byYear = new Map<number, number>();
    for (const d of allDeaths) {
      const y = yearOf(d.incident_date);
      if (y === null) continue;
      byYear.set(y, (byYear.get(y) ?? 0) + 1);
    }
    return Array.from(byYear.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([year, n]) => ({ year, n, current: year === currentYear }));
  }, [allDeaths, currentYear]);

  if (!memorial) {
    return <div style={{ minHeight: "100vh" }} />;
  }

  return (
    <>
      <nav className="top">
        <div className="wrap row">
          <Link href="/" className="brand">سجل الضحايا</Link>
          <div className="links">
            <a href="#cases">القضايا</a>
            <a href="#regions">المناطق</a>
            <a href="#years">السنوات</a>
            <a href="#about">عن المشروع</a>
          </div>
        </div>
      </nav>

      <header className="hero">
        <div className="wrap">
          <div className="eyebrow">سجل عام · يُحدَّث أسبوعياً</div>
          <h1>كلّ ضحيّة لها اسم.<br />وكل قضيّة لها قصّة.</h1>
          <p className="lede">
            سجلٌّ عامّ يُوثّق ضحايا جرائم القتل في المجتمع العربي في إسرائيل، اسماً تلو الآخر، استناداً إلى مصادر إخباريّة بالعربيّة والعبريّة.
          </p>
        </div>
      </header>

      <section className="stats wrap">
        <div className="stat">
          <div className="num">{lastYearCount || "—"}</div>
          <div className="label"><strong>ضحيّة في عام {currentYear - 1}</strong> — حسب السجلّ.</div>
        </div>
        <div className="stat">
          <div className="num" style={{ color: "var(--blood)" }}>{currentYearCount}</div>
          <div className="label">ضحيّة منذ بداية {currentYear}<br />حتى الآن.</div>
        </div>
        <div className="stat">
          <div className="num">{totalAll}</div>
          <div className="label">قضيّة موثّقة في السجلّ<br />منذ بدء التوثيق.</div>
        </div>
        <div className="stat">
          <div className="num">{under40Pct}%</div>
          <div className="label">من الضحايا أعمارهم<br />دون الأربعين عاماً.</div>
        </div>
      </section>

      <section className="sec" id="cases">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">القضايا الأحدث</h2>
            <div className="sec-meta">عرض كل القضايا</div>
          </div>

          <div className="filters">
            <button
              className={`filter ${activeFilter === "all" ? "on" : ""}`}
              onClick={() => setActiveFilter("all")}
            >
              الكلّ <span className="count">{totalAll}</span>
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
                {REGION_LABELS_AR[r]} <span className="count">{regionCounts[r]}</span>
              </button>
            ))}
          </div>

          <div className="cases">
            {recentCases.map((d) => (
              <Link
                key={`${d.run_id}-${d.case_index}`}
                href={`/cases/${d.run_id}/${d.case_index}`}
                className="case"
              >
                <div className="date">{formatArabicDate(d.incident_date)}</div>
                <div className="name">
                  {d.victim_name_ar || d.victim_name_he || d.victim_name || "—"}
                </div>
                <div className="meta">
                  {d.victim_age !== null && <span>{d.victim_age} عاماً</span>}
                  {d.victim_age !== null && <span className="sep">·</span>}
                  <span>{d.city_ar || d.city}</span>
                </div>
                <div className="footer">
                  <span className="badge">قيد التوثيق</span>
                  <span className="arrow">←</span>
                </div>
              </Link>
            ))}
            {recentCases.length === 0 && (
              <div style={{ gridColumn: "1 / -1", padding: "48px 24px", textAlign: "center", color: "var(--muted)" }}>
                لا توجد قضايا تطابق هذه التصفية.
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="sec" id="regions">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">حسب المنطقة</h2>
            <div className="sec-meta">منذ بداية التوثيق</div>
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
                <div className="name">{REGION_LABELS_AR[r]}</div>
                <div className="num">
                  <strong>{regionCounts[r]}</strong>
                  <span className="since">ضحيّة</span>
                </div>
                <div className="bar">
                  <div className="fl" style={{ width: `${(regionCounts[r] / maxRegion) * 100}%` }} />
                </div>
              </a>
            ))}
          </div>
        </div>
      </section>

      <section className="sec" id="years">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">وراء كلّ رقم، إنسان</h2>
            <div className="sec-meta">
              كلّ علامة
              <span className="tap-legend" style={{ display: "inline-flex" }}>
                <span className="swatch" />
              </span>
              تُمثّل ضحيّة واحدة
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
                        حتى الآن
                      </span>
                    )}
                  </div>
                  <div className="tap-count">
                    <strong>{d.n}</strong> ضحيّة
                  </div>
                </div>
                <div className="tap-dots">
                  {Array.from({ length: d.n }).map((_, i) => (
                    <span key={i} className="dot-cell" title={`ضحيّة في ${d.year}`} />
                  ))}
                </div>
              </div>
            ))}
            {yearlyData.length === 0 && (
              <p style={{ color: "var(--muted)" }}>لا توجد بيانات سنويّة.</p>
            )}
          </div>

          {yearlyData.length > 0 && (
            <p className="tap-caption">
              كلّ علامةٍ هنا كانت إنساناً — أُماً أو أباً، ابناً أو ابنةً، صديقاً أو جاراً.
              هذا السجلّ موجود لئلّا يُنسى أحد منهم.
            </p>
          )}
        </div>
      </section>

      <section className="sec" id="about">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">عن المشروع</h2>
          </div>

          <div className="about-grid">
            <div className="about-col">
              <h3>اسم لكلّ ضحيّة</h3>
              <p>لا يُختزل أحدٌ إلى رقمٍ في إحصاء. كلّ قضيّة في هذا السجل تحمل اسماً ومدينةً وتاريخاً، وما أمكن جمعه من تفاصيل من مصادرها الأصليّة.</p>
            </div>
            <div className="about-col">
              <h3>من مصادر متعدّدة</h3>
              <p>نجمع المعلومات من مواقع إخباريّة بالعربيّة والعبريّة (عرب 48، واي نت، والّا، وغيرها)، ونحفظ الأسماء كما وردت بلغاتها الأصليّة دون تحويلها.</p>
            </div>
            <div className="about-col">
              <h3>شفافيّة في الشك</h3>
              <p>عندما تتعارض المصادر أو تكون المعلومات ناقصة، نُشير إلى ذلك بوضوح. الصدق في ما لا نعرفه جزء من احترامنا للضحايا وعائلاتهم.</p>
            </div>
          </div>
        </div>
      </section>

      <footer className="bottom">
        <div className="wrap">
          <p>سجلٌّ عام مستقلّ · لا يُمثّل أيّ جهةٍ رسميّة.</p>
        </div>
      </footer>
    </>
  );
}
