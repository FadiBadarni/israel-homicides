"use client";

interface DeathCountProps {
  count: number;
}

export function DeathCount({ count }: DeathCountProps) {
  return (
    <div className="absolute bottom-4 right-4 z-20 text-xs text-neutral-700 bg-white/80 backdrop-blur px-2 py-1 rounded">
      <span className="tabular-nums font-semibold">{count}</span>{" "}
      <span className="text-neutral-500">{count === 1 ? "name" : "names"}</span>
    </div>
  );
}
