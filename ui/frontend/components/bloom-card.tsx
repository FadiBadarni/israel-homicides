"use client";

import { useEffect, useState } from "react";
import type { CaseDetail, DeathSummary, Locality } from "@/lib/api";
import { fetchCase } from "@/lib/api";
import { BidiName } from "./bidi-name";

interface BloomCardProps {
  locality: Locality;
  initialCaseIndex: number | null;
  onClose: () => void;
  onSelectCase: (caseIndex: number | null) => void;
}

export function BloomCard({
  locality,
  initialCaseIndex,
  onClose,
  onSelectCase,
}: BloomCardProps) {
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialCaseIndex === null) {
      setCaseDetail(null);
      return;
    }
    const target = locality.deaths.find((d) => d.case_index === initialCaseIndex);
    const runId = target?.run_id ?? locality.deaths[0]?.run_id;
    if (!runId) {
      setError("Missing run_id");
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    setError(null);
    fetchCase(runId, initialCaseIndex)
      .then((d) => alive && setCaseDetail(d))
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [initialCaseIndex, locality]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="max-w-md mx-auto bg-white rounded-lg border border-neutral-300 shadow-sm">
      <header className="px-4 py-3 border-b border-neutral-200 flex items-center justify-between">
        <div className="space-y-0.5">
          <h2 className="text-sm font-semibold">
            <BidiName he={locality.city_he} ar={locality.city_ar} en={locality.city} />
          </h2>
          <p className="text-xs text-neutral-500">
            {locality.death_count} {locality.death_count === 1 ? "name" : "names"}
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="text-neutral-400 hover:text-neutral-700"
        >
          ×
        </button>
      </header>

      {initialCaseIndex === null ? (
        <LocalityList deaths={locality.deaths} onSelect={onSelectCase} />
      ) : loading ? (
        <p className="p-4 text-xs text-neutral-500">Loading…</p>
      ) : error ? (
        <p className="p-4 text-xs text-red-700">Unable to load case detail.</p>
      ) : caseDetail ? (
        <CaseDetailBody c={caseDetail} onBack={() => onSelectCase(null)} />
      ) : null}
    </div>
  );
}

function LocalityList({
  deaths,
  onSelect,
}: {
  deaths: DeathSummary[];
  onSelect: (caseIndex: number) => void;
}) {
  return (
    <ul className="divide-y divide-neutral-100">
      {deaths.map((d) => (
        <li key={d.case_index}>
          <button
            onClick={() => onSelect(d.case_index)}
            className="w-full text-left px-4 py-2.5 hover:bg-neutral-50 flex items-baseline justify-between gap-2"
          >
            <span className="text-sm">
              <BidiName he={d.victim_name_he} ar={d.victim_name_ar} en={d.victim_name} />
              {d.victim_age !== null && (
                <span className="text-neutral-500 text-xs ml-1">· {d.victim_age}</span>
              )}
            </span>
            {d.incident_date && (
              <span className="text-xs text-neutral-400 tabular-nums">{d.incident_date}</span>
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}

function CaseDetailBody({ c, onBack }: { c: CaseDetail; onBack: () => void }) {
  return (
    <div className="p-4 space-y-3 text-sm">
      <button onClick={onBack} className="text-xs text-neutral-500 hover:text-neutral-900">
        ← back to locality
      </button>

      <h3 className="text-base font-semibold leading-tight">
        <BidiName he={c.victim_name_he} ar={c.victim_name_ar} en={c.victim_name} />
      </h3>

      <dl className="space-y-1 text-xs">
        {c.victim_age !== null && <DetailRow label="Age" value={String(c.victim_age)} />}
        {c.incident_date && <DetailRow label="Incident" value={c.incident_date} />}
        {c.death_date && <DetailRow label="Died" value={c.death_date} />}
        {c.weapon_type && <DetailRow label="Weapon" value={c.weapon_type} />}
        {c.suspect_status && <DetailRow label="Suspect" value={c.suspect_status} />}
        {c.legal_status && <DetailRow label="Legal" value={c.legal_status} />}
      </dl>

      {c.case_narrative && (
        <p className="text-xs text-neutral-600 border-t border-neutral-100 pt-2 leading-relaxed">
          {c.case_narrative}
        </p>
      )}

      {c.sources.length > 0 && (
        <div className="space-y-1 border-t border-neutral-100 pt-2">
          <p className="text-xs font-medium">Sources</p>
          <ul className="space-y-1">
            {c.sources.map((s, i) => (
              <li key={i} className="text-xs">
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-700 hover:underline break-all"
                >
                  {s.domain}
                </a>
                {s.published_at && <span className="text-neutral-400"> · {s.published_at}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-neutral-500 w-20 flex-shrink-0">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
