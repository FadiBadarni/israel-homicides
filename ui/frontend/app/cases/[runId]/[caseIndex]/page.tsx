"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { fetchCase, type CaseDetail } from "@/lib/api";
import { formatArabicDate } from "@/lib/format";

interface PageProps {
  params: Promise<{ runId: string; caseIndex: string }>;
}

export default function CaseDetailPage({ params }: PageProps) {
  const { runId, caseIndex } = use(params);
  const [c, setC] = useState<CaseDetail | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetchCase(runId, Number(caseIndex))
      .then((d) => {
        setC(d);
        setLoaded(true);
      })
      .catch(() => {
        setC(null);
        setLoaded(true);
      });
  }, [runId, caseIndex]);

  if (!loaded) {
    return <div style={{ minHeight: "100vh" }} />;
  }

  if (!c) {
    return (
      <div className="case-page">
        <div className="breadcrumb">
          <Link href="/">سجل ضحايا الجريمة في المجتمع العربي في إسرائيل</Link>
        </div>
        <p style={{ textAlign: "center", marginTop: 96, color: "var(--muted)" }}>
          تعذّر تحميل القضيّة.
        </p>
      </div>
    );
  }

  const primaryName = c.victim_name_ar || c.victim_name_he || c.victim_name || "—";

  return (
    <div className="case-page">
      <div className="breadcrumb">
        <Link href="/">سجل ضحايا الجريمة في المجتمع العربي في إسرائيل</Link>
      </div>

      <header className="masthead">
        <div className="age-label">في ذكرى</div>
        <h1 className="case-name">{primaryName}</h1>
        <div className="lifespan">
          {c.victim_age !== null && (
            <>
              <span>{c.victim_age} عاماً</span>
              <span className="lsep">·</span>
            </>
          )}
          {c.city && (
            <>
              <span>من {c.city}</span>
              <span className="lsep">·</span>
            </>
          )}
          {c.incident_date && <span>قُتل في {formatArabicDate(c.incident_date)}</span>}
        </div>
      </header>

      {c.case_narrative && (
        <p className="case-summary">{c.case_narrative}</p>
      )}

      <div className="rule" />

      <section>
        <div className="section-label">تفاصيل الحادثة</div>
        <div className="facts">
          <div className="fact">
            <div className="k">التاريخ</div>
            <div className="v">{formatArabicDate(c.incident_date) || "—"}</div>
          </div>
          <div className="fact">
            <div className="k">المكان</div>
            <div className="v">{c.city || "—"}</div>
            {c.district && <div className="sub">{c.district}</div>}
          </div>
          <div className="fact">
            <div className="k">السبب</div>
            <div className="v">{c.weapon_type || "—"}</div>
          </div>
          <div className="fact">
            <div className="k">حالة المشتبه به</div>
            <div className="v small">{c.suspect_status || "—"}</div>
          </div>
        </div>
      </section>

      {c.sources.length > 0 && (
        <>
          <div className="rule" />
          <section>
            <div className="section-label">المصادر</div>
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
        <p>هذه الصفحة جزء من سجلٍّ عام يوثّق ضحايا الجريمة في المجتمع العربي في إسرائيل.</p>
        <p>الأسماء والتفاصيل مُحفوظة كما وردت في مصادرها الأصليّة.</p>
      </footer>
    </div>
  );
}
