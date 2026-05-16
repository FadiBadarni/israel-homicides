"use client";

import { useEffect, useRef, useState } from "react";

const DEFAULT_DURATION_MS = 1600;

/** Quartic ease-out: fast start, slow settle. Gives the number a
 *  "weighing itself in" rhythm rather than a marketing-y bounce. */
const easeOutQuart = (t: number) => 1 - Math.pow(1 - t, 4);

interface CountUpProps {
  value: number;
  durationMs?: number;
  /** Optional formatter — defaults to a rounded integer. */
  format?: (n: number) => string;
}

/**
 * Animates from the currently displayed value to `value` over
 * `durationMs`. The first time the component mounts the start is 0,
 * so a stat that lands on 137 ticks 0 → 137. Subsequent value changes
 * animate from the last displayed value (rarely needed for stats but
 * keeps it correct if the data does refresh).
 *
 * Honors prefers-reduced-motion: jumps to the final value with no
 * animation.
 */
export function CountUp({
  value,
  durationMs = DEFAULT_DURATION_MS,
  format,
}: CountUpProps) {
  const [display, setDisplay] = useState(0);
  // Mirror of `display` for the animation effect to read without
  // subscribing to it (would cause the effect to retrigger every frame).
  const displayRef = useRef(0);
  useEffect(() => {
    displayRef.current = display;
  }, [display]);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      setDisplay(value);
      return;
    }
    const from = displayRef.current;
    if (from === value) return;
    const start = performance.now();

    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const next = from + (value - from) * easeOutQuart(t);
      setDisplay(next);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [value, durationMs]);

  return <>{format ? format(display) : Math.round(display).toString()}</>;
}
