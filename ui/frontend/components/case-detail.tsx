"use client";

import Link from "next/link";
import type { CaseDetail } from "@/lib/api";
import { ConfidenceBadge } from "./confidence-badge";
import { BidiName } from "./bidi-name";
import { OutcomeBadge } from "./outcome-badge";
import { MediaGallery } from "./media-gallery";

interface CaseDetailProps {
  caseData: CaseDetail;
}

export function CaseDetailView({ caseData: c }: CaseDetailProps) {
  const flagged = (c.flags ?? []).includes("flagged_for_review");
  const allMedia = [...(c.media_evidence ?? []), ...(c.media ?? [])];

  return (
    <div className="space-y-4">
      {/* Back nav */}
      <Link href="/cases" className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1">
        ← Back to cases
      </Link>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* ── Column 1: Victim card + flags ── */}
        <div className="space-y-4">
          <div className="rounded-xl border bg-card p-4 space-y-3">
            <div className="flex items-start justify-between gap-2">
              <h1 className="text-lg font-semibold leading-tight">
                <BidiName he={c.victim_name_he} ar={c.victim_name_ar} en={c.victim_name} />
              </h1>
              <OutcomeBadge outcome={c.victim_outcome ?? null} />
            </div>

            <dl className="text-sm space-y-1.5">
              {c.victim_age && <DetailRow label="Age" value={String(c.victim_age)} />}
              {c.victim_gender && <DetailRow label="Gender" value={c.victim_gender} />}
              {c.incident_date && <DetailRow label="Date" value={c.incident_date} />}
              {c.death_date && <DetailRow label="Death date" value={c.death_date} />}
              {c.city && <DetailRow label="City" value={c.city} bidi />}
              {c.neighborhood && <DetailRow label="Neighborhood" value={c.neighborhood} bidi />}
              {c.district && <DetailRow label="District" value={c.district} bidi />}
              {c.region && <DetailRow label="Region" value={c.region} />}
              {c.weapon_type && <DetailRow label="Weapon" value={c.weapon_type} />}
              {c.suspect_status && <DetailRow label="Suspect" value={c.suspect_status} />}
              {c.legal_status && <DetailRow label="Legal" value={c.legal_status} />}
            </dl>

            {c.case_narrative && (
              <p className="text-sm text-muted-foreground border-t pt-3 leading-relaxed">
                {c.case_narrative}
              </p>
            )}
          </div>

          {/* Flags */}
          {(c.flags ?? []).length > 0 && (
            <div className="rounded-xl border bg-amber-50 p-4 space-y-1.5">
              <h2 className="text-sm font-semibold text-amber-900">Flags</h2>
              <ul className="space-y-1">
                {(c.flags ?? []).map((f) => (
                  <li key={f} className="text-xs text-amber-800 flex items-center gap-1.5">
                    <span className="text-amber-500">⚠</span>
                    {f.replace(/_/g, " ")}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Aliases */}
          {(c.aliases ?? []).length > 0 && (
            <div className="rounded-xl border bg-card p-4 space-y-1.5">
              <h2 className="text-sm font-semibold">Name aliases</h2>
              <ul className="space-y-1">
                {(c.aliases ?? []).map((a, i) => (
                  <li key={i} className="text-sm text-muted-foreground">
                    <bdi dir="auto">{a}</bdi>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* ── Column 2: Confidence + Sources ── */}
        <div className="space-y-4">
          <div className="rounded-xl border bg-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">Confidence</h2>
              <ConfidenceBadge score={c.confidence_score} flagged={flagged} />
            </div>

            {c.review_status && (
              <p className="text-xs text-muted-foreground">
                Review status: <span className="font-medium">{c.review_status}</span>
              </p>
            )}

            {c.canonical_case_id && (
              <p className="text-xs text-muted-foreground break-all">
                Case ID: <span className="font-mono">{c.canonical_case_id}</span>
              </p>
            )}
          </div>

          {/* Sources */}
          {(c.sources ?? []).length > 0 && (
            <div className="rounded-xl border bg-card p-4 space-y-3">
              <h2 className="text-sm font-semibold">
                Sources <span className="text-muted-foreground font-normal">({c.sources.length})</span>
              </h2>
              <ul className="space-y-3">
                {c.sources.map((s, i) => (
                  <li key={i} className="text-sm space-y-0.5">
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline text-xs break-all"
                    >
                      {s.title || s.url}
                    </a>
                    <div className="flex gap-2 text-xs text-muted-foreground">
                      {s.domain && <span>{s.domain}</span>}
                      {s.published_at && <span>· {s.published_at}</span>}
                      {s.role && <span>· {s.role}</span>}
                      {s.tier !== null && s.tier !== undefined && (
                        <span>· tier {s.tier}</span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Conflict map */}
          {c.conflict_map && Object.keys(c.conflict_map).length > 0 && (
            <div className="rounded-xl border bg-orange-50 p-4 space-y-2">
              <h2 className="text-sm font-semibold text-orange-900">Field conflicts</h2>
              <ul className="space-y-1">
                {Object.entries(c.conflict_map).map(([field, vals]) => (
                  <li key={field} className="text-xs text-orange-800">
                    <span className="font-medium">{field}:</span>{" "}
                    {JSON.stringify(vals)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* ── Column 3: Media ── */}
        <div className="space-y-4">
          <div className="rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold mb-3">
              Media <span className="text-muted-foreground font-normal">({allMedia.length})</span>
            </h2>
            {allMedia.length > 0 ? (
              <MediaGallery
                items={(c.media_evidence ?? []).length > 0 ? c.media_evidence! : allMedia}
                label={(c.media_evidence ?? []).length > 0 ? "Evidence media" : undefined}
              />
            ) : (
              <div className="text-sm text-muted-foreground">No media</div>
            )}
            {(c.media ?? []).length > 0 && (c.media_evidence ?? []).length > 0 && (
              <div className="mt-3">
                <MediaGallery items={c.media!} label="All media" />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function DetailRow({ label, value, bidi }: { label: string; value: string; bidi?: boolean }) {
  return (
    <div className="flex gap-2">
      <dt className="text-muted-foreground w-28 flex-shrink-0">{label}</dt>
      <dd className="font-medium">
        {bidi ? <bdi dir="auto">{value}</bdi> : value}
      </dd>
    </div>
  );
}
