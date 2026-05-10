"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useTransition } from "react";
import type { FiltersResponse, RunMeta } from "@/lib/api";

interface CaseFiltersProps {
  filters: FiltersResponse;
  runs: RunMeta[];
  activeRunId: string;
}

export function CaseFilters({ filters, runs, activeRunId }: CaseFiltersProps) {
  const router = useRouter();
  const params = useSearchParams();
  const [, startTransition] = useTransition();

  const update = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) {
        next.set(key, value);
      } else {
        next.delete(key);
      }
      next.delete("page"); // reset to page 1 on filter change
      startTransition(() => {
        router.push(`/cases?${next.toString()}`);
      });
    },
    [params, router]
  );

  const clearAll = useCallback(() => {
    startTransition(() => {
      router.push("/cases");
    });
  }, [router]);

  const hasFilters = ["run_id", "city", "outcome", "weapon_type", "review_status", "min_confidence", "search", "date_from", "date_to", "flagged"].some(
    (k) => params.has(k)
  );

  return (
    <aside className="w-56 flex-shrink-0 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Filters</h2>
        {hasFilters && (
          <button
            onClick={clearAll}
            className="text-xs text-muted-foreground underline hover:text-foreground"
          >
            Clear all
          </button>
        )}
      </div>

      {/* Run selector */}
      {runs.length > 0 && (
        <FilterSection label="Run">
          <select
            value={activeRunId}
            onChange={(e) => update("run_id", e.target.value)}
            className="w-full rounded-md border bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="">All runs</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id} ({r.case_count})
              </option>
            ))}
          </select>
        </FilterSection>
      )}

      {/* Search */}
      <FilterSection label="Name search">
        <input
          type="text"
          placeholder="Search…"
          defaultValue={params.get("search") ?? ""}
          className="w-full rounded-md border bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          onChange={(e) => update("search", e.target.value)}
        />
      </FilterSection>

      {/* City */}
      {filters.cities.length > 0 && (
        <FilterSection label="City">
          <FilterSelect
            value={params.get("city") ?? ""}
            options={filters.cities}
            placeholder="All cities"
            onChange={(v) => update("city", v)}
          />
        </FilterSection>
      )}

      {/* Outcome */}
      {filters.outcomes.length > 0 && (
        <FilterSection label="Outcome">
          <FilterSelect
            value={params.get("outcome") ?? ""}
            options={filters.outcomes}
            placeholder="All outcomes"
            onChange={(v) => update("outcome", v)}
          />
        </FilterSection>
      )}

      {/* Weapon */}
      {filters.weapon_types.length > 0 && (
        <FilterSection label="Weapon">
          <FilterSelect
            value={params.get("weapon_type") ?? ""}
            options={filters.weapon_types}
            placeholder="All weapons"
            onChange={(v) => update("weapon_type", v)}
          />
        </FilterSection>
      )}

      {/* Review status */}
      {filters.review_statuses.length > 0 && (
        <FilterSection label="Review status">
          <FilterSelect
            value={params.get("review_status") ?? ""}
            options={filters.review_statuses}
            placeholder="All statuses"
            onChange={(v) => update("review_status", v)}
          />
        </FilterSection>
      )}

      {/* Confidence slider */}
      <FilterSection label={`Min confidence: ${params.get("min_confidence") ?? "0"}%`}>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          defaultValue={params.get("min_confidence") ? String(Number(params.get("min_confidence")) * 100) : "0"}
          className="w-full accent-primary"
          onChange={(e) => update("min_confidence", String(Number(e.target.value) / 100))}
        />
      </FilterSection>

      {/* Date range */}
      <FilterSection label="Date from">
        <input
          type="date"
          defaultValue={params.get("date_from") ?? ""}
          className="w-full rounded-md border bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          onChange={(e) => update("date_from", e.target.value)}
        />
      </FilterSection>
      <FilterSection label="Date to">
        <input
          type="date"
          defaultValue={params.get("date_to") ?? ""}
          className="w-full rounded-md border bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          onChange={(e) => update("date_to", e.target.value)}
        />
      </FilterSection>

      {/* Flagged toggle */}
      <FilterSection label="">
        <label className="flex items-center gap-2 cursor-pointer text-sm">
          <input
            type="checkbox"
            checked={params.get("flagged") === "true"}
            onChange={(e) => update("flagged", e.target.checked ? "true" : "")}
            className="rounded border-border"
          />
          Flagged only
        </label>
      </FilterSection>
    </aside>
  );
}

function FilterSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      {label && <label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</label>}
      {children}
    </div>
  );
}

function FilterSelect({
  value,
  options,
  placeholder,
  onChange,
}: {
  value: string;
  options: string[];
  placeholder: string;
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
    >
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}
