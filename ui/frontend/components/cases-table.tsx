"use client";

import { useRouter, useSearchParams } from "next/navigation";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { useState, useTransition } from "react";
import type { CaseSummary } from "@/lib/api";
import { ConfidenceBadge } from "./confidence-badge";
import { BidiName } from "./bidi-name";
import { OutcomeBadge } from "./outcome-badge";
import { cn } from "@/lib/utils";

const col = createColumnHelper<CaseSummary>();

const columns = [
  col.accessor((row) => ({ he: row.victim_name_he, ar: row.victim_name_ar, en: row.victim_name }), {
    id: "victim_name",
    header: "Victim",
    cell: (info) => {
      const v = info.getValue();
      return <BidiName he={v.he} ar={v.ar} en={v.en} className="font-medium" />;
    },
  }),
  col.accessor("victim_age", {
    header: "Age",
    cell: (info) => info.getValue() ?? "—",
  }),
  col.accessor("victim_outcome", {
    header: "Outcome",
    cell: (info) => <OutcomeBadge outcome={info.getValue() ?? null} />,
  }),
  col.accessor("incident_date", {
    header: "Date",
    cell: (info) => {
      const d = info.getValue();
      return d ? <span className="tabular-nums">{d}</span> : "—";
    },
  }),
  col.accessor("city", {
    header: "City",
    cell: (info) => {
      const city = info.getValue();
      return city ? <bdi dir="auto">{city}</bdi> : "—";
    },
  }),
  col.accessor("weapon_type", {
    header: "Weapon",
    cell: (info) => info.getValue() ?? "—",
  }),
  col.accessor("source_count", {
    header: "Sources",
    cell: (info) => (
      <span className="tabular-nums text-muted-foreground">{info.getValue()}</span>
    ),
  }),
  col.accessor("confidence_score", {
    header: "Confidence",
    cell: (info) => {
      const row = info.row.original;
      const flagged = (row.flags ?? []).includes("flagged_for_review");
      return <ConfidenceBadge score={info.getValue()} flagged={flagged} size="sm" />;
    },
  }),
  col.accessor("flags", {
    header: "Flags",
    cell: (info) => {
      const flags = info.getValue() ?? [];
      if (!flags.length) return null;
      return (
        <div className="flex flex-wrap gap-1">
          {flags.slice(0, 2).map((f: string) => (
            <span key={f} className="rounded bg-amber-100 text-amber-800 text-[10px] px-1 py-0.5 border border-amber-200">
              {f.replace(/_/g, " ")}
            </span>
          ))}
          {flags.length > 2 && (
            <span className="text-[10px] text-muted-foreground">+{flags.length - 2}</span>
          )}
        </div>
      );
    },
    enableSorting: false,
  }),
];

interface CasesTableProps {
  cases: CaseSummary[];
  total: number;
  page: number;
  pages: number;
}

export function CasesTable({ cases, total, page, pages }: CasesTableProps) {
  const router = useRouter();
  const params = useSearchParams();
  const [, startTransition] = useTransition();
  const [sorting, setSorting] = useState<SortingState>([
    { id: params.get("sort_by") ?? "incident_date", desc: (params.get("sort_dir") ?? "desc") === "desc" },
  ]);

  const table = useReactTable({
    data: cases,
    columns,
    state: { sorting },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      setSorting(next);
      if (next.length) {
        const p = new URLSearchParams(params.toString());
        p.set("sort_by", next[0].id);
        p.set("sort_dir", next[0].desc ? "desc" : "asc");
        p.delete("page");
        startTransition(() => router.push(`/cases?${p.toString()}`));
      }
    },
    manualSorting: true,
    getCoreRowModel: getCoreRowModel(),
  });

  const navigate = (p: number) => {
    const next = new URLSearchParams(params.toString());
    next.set("page", String(p));
    startTransition(() => router.push(`/cases?${next.toString()}`));
  };

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted-foreground">
        {total} case{total !== 1 ? "s" : ""} · page {page} of {pages}
      </div>

      <div className="rounded-lg border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 border-b">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => (
                  <th
                    key={header.id}
                    className={cn(
                      "px-3 py-2 text-left font-medium text-muted-foreground",
                      header.column.getCanSort() && "cursor-pointer select-none hover:text-foreground"
                    )}
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {header.column.getIsSorted() === "asc" && " ↑"}
                    {header.column.getIsSorted() === "desc" && " ↓"}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className="border-b last:border-0 hover:bg-muted/30 cursor-pointer transition-colors"
                onClick={() =>
                  startTransition(() =>
                    router.push(`/cases/${row.original.run_id}/${row.original.case_index}`)
                  )
                }
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2.5 align-middle">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {cases.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-3 py-8 text-center text-muted-foreground">
                  No cases match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <div className="flex items-center gap-1 text-sm">
          <button
            onClick={() => navigate(1)}
            disabled={page <= 1}
            className="px-2 py-1 rounded border hover:bg-muted disabled:opacity-40"
          >
            «
          </button>
          <button
            onClick={() => navigate(page - 1)}
            disabled={page <= 1}
            className="px-2 py-1 rounded border hover:bg-muted disabled:opacity-40"
          >
            ‹
          </button>
          <span className="px-2 text-muted-foreground">
            {page} / {pages}
          </span>
          <button
            onClick={() => navigate(page + 1)}
            disabled={page >= pages}
            className="px-2 py-1 rounded border hover:bg-muted disabled:opacity-40"
          >
            ›
          </button>
          <button
            onClick={() => navigate(pages)}
            disabled={page >= pages}
            className="px-2 py-1 rounded border hover:bg-muted disabled:opacity-40"
          >
            »
          </button>
        </div>
      )}
    </div>
  );
}
