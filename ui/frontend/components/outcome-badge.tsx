"use client";

import { cn } from "@/lib/utils";

const OUTCOME_STYLES: Record<string, string> = {
  died: "bg-red-100 text-red-800 border-red-300",
  critical: "bg-orange-100 text-orange-800 border-orange-300",
  survived: "bg-blue-100 text-blue-800 border-blue-300",
  unknown: "bg-gray-100 text-gray-600 border-gray-300",
};

const OUTCOME_LABELS: Record<string, string> = {
  died: "Died",
  critical: "Critical",
  survived: "Survived",
  unknown: "Unknown",
};

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return null;
  const style = OUTCOME_STYLES[outcome] ?? OUTCOME_STYLES.unknown;
  const label = OUTCOME_LABELS[outcome] ?? outcome;
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium", style)}>
      {label}
    </span>
  );
}
