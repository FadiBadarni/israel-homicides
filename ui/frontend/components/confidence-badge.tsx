"use client";

import { cn } from "@/lib/utils";

interface ConfidenceBadgeProps {
  score: number | null;
  flagged?: boolean;
  size?: "sm" | "md";
}

export function ConfidenceBadge({ score, flagged, size = "md" }: ConfidenceBadgeProps) {
  if (score === null || score === undefined) {
    return (
      <span className={cn(
        "inline-flex items-center rounded-full font-mono border",
        size === "sm" ? "text-xs px-1.5 py-0.5" : "text-sm px-2 py-1",
        "bg-muted text-muted-foreground border-border"
      )}>
        —
      </span>
    );
  }

  const pct = Math.round(score * 100);

  const colorClass =
    score >= 0.85
      ? "bg-green-100 text-green-800 border-green-300 dark:bg-green-900/30 dark:text-green-300"
      : score >= 0.65
      ? "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-900/30 dark:text-amber-300"
      : "bg-red-100 text-red-800 border-red-300 dark:bg-red-900/30 dark:text-red-300";

  return (
    <span className={cn(
      "inline-flex items-center gap-1 rounded-full font-mono border",
      size === "sm" ? "text-xs px-1.5 py-0.5" : "text-sm px-2 py-1",
      colorClass
    )}>
      {flagged && <span title="Flagged for review">⚠</span>}
      {pct}%
    </span>
  );
}
