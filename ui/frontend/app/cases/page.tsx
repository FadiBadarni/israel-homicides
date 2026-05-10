import { Suspense } from "react";
import { fetchCases, fetchFilters, fetchRuns } from "@/lib/api";
import { CaseFilters } from "@/components/case-filters";
import { CasesTable } from "@/components/cases-table";

interface PageProps {
  searchParams: Promise<Record<string, string>>;
}

export default async function CasesPage({ searchParams }: PageProps) {
  const sp = await searchParams;

  // Default to the latest run to avoid cross-run duplicates.
  // User can switch via the run selector or clear to see all.
  const runs = await fetchRuns();
  const latestRunId = runs[0]?.run_id ?? undefined;
  const activeRunId = sp.run_id !== undefined ? (sp.run_id || undefined) : latestRunId;

  const page = Number(sp.page ?? 1);
  const limit = Number(sp.limit ?? 50);

  const [casesRes, filters] = await Promise.all([
    fetchCases({
      page,
      limit,
      run_id: activeRunId,
      city: sp.city,
      district: sp.district,
      outcome: sp.outcome,
      weapon_type: sp.weapon_type,
      search: sp.search,
      min_confidence: sp.min_confidence ? Number(sp.min_confidence) : undefined,
      review_status: sp.review_status,
      date_from: sp.date_from,
      date_to: sp.date_to,
      flagged: sp.flagged === "true" ? true : sp.flagged === "false" ? false : undefined,
      sort_by: sp.sort_by ?? "incident_date",
      sort_dir: (sp.sort_dir as "asc" | "desc") ?? "desc",
    }),
    fetchFilters(activeRunId),
  ]);

  return (
    <div className="flex gap-6">
      <Suspense>
        <CaseFilters filters={filters} runs={runs} activeRunId={activeRunId ?? ""} />
      </Suspense>

      <div className="flex-1 min-w-0">
        <Suspense fallback={<TableSkeleton />}>
          <CasesTable
            cases={casesRes.cases}
            total={casesRes.total}
            page={casesRes.page}
            pages={casesRes.pages}
          />
        </Suspense>
      </div>
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="space-y-2 animate-pulse">
      <div className="h-8 rounded bg-muted w-32" />
      <div className="rounded-lg border overflow-hidden">
        <div className="h-10 bg-muted/50" />
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="h-12 border-t bg-muted/20" />
        ))}
      </div>
    </div>
  );
}
