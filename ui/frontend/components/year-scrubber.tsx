"use client";

interface YearScrubberProps {
  min: number;
  max: number;
  from: number;
  to: number;
  onChange: (from: number, to: number) => void;
}

export function YearScrubber({ min, max, from, to, onChange }: YearScrubberProps) {
  return (
    <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 w-80 max-w-[80vw] bg-white/80 backdrop-blur rounded px-3 py-2 space-y-1">
      <div className="flex justify-between text-[10px] text-neutral-500 tabular-nums">
        <span>{from}</span>
        <span>{to}</span>
      </div>
      <div className="flex gap-2 items-center">
        <input
          type="range"
          min={min}
          max={max}
          step={1}
          value={from}
          onChange={(e) => onChange(Math.min(Number(e.target.value), to), to)}
          className="flex-1 accent-neutral-700"
          aria-label="Year from"
        />
        <input
          type="range"
          min={min}
          max={max}
          step={1}
          value={to}
          onChange={(e) => onChange(from, Math.max(Number(e.target.value), from))}
          className="flex-1 accent-neutral-700"
          aria-label="Year to"
        />
      </div>
    </div>
  );
}
