"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Lang } from "@/lib/language-context";
import { t, pickNameWithTransliteration, pickLangField } from "@/lib/i18n";
import { formatDate } from "@/lib/format";

export interface VigilCase {
  run_id: string;
  case_index: number;
  victim_name_ar: string | null;
  victim_name_he: string | null;
  victim_name_en?: string | null;
  name_transliterations?: ReadonlyArray<{
    value: string;
    target_script: "ar" | "he" | "en";
    source_script: "ar" | "he" | "en";
    method: "dictionary" | "rule_based";
  }>;
  victim_age: number | null;
  incident_date: string | null;
  city_ar: string | null;
  city_he: string | null;
  city_en: string;
}

interface VigilHeroProps {
  cases: VigilCase[];
  lang: Lang;
}

const ROTATE_MS = 8000;

/**
 * Vigil hero: rotates through eligible victims one at a time, each shown
 * poster-style. Reads the dataset before the page explains itself.
 *
 * - Shuffles cases once on mount so every visit surfaces a different order.
 * - Auto-advances every ROTATE_MS, pauses while the mouse is over the hero.
 * - The progress bar at the bottom mirrors the timer and pauses with the
 *   rotation (animation-play-state) when the user is hovering.
 * - prefers-reduced-motion: no rotation, no progress bar — just the first
 *   eligible case held statically.
 */
export function VigilHero({ cases, lang }: VigilHeroProps) {
  const [index, setIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);

  const shuffled = useMemo(() => {
    if (cases.length === 0) return [];
    const arr = [...cases];
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }, [cases]);

  useEffect(() => {
    if (shuffled.length <= 1 || isPaused) return;
    const id = window.setInterval(() => {
      setIndex((i) => (i + 1) % shuffled.length);
    }, ROTATE_MS);
    return () => window.clearInterval(id);
  }, [shuffled.length, isPaused]);

  // Reserve the hero's vertical footprint while data loads so the rest
  // of the page doesn't jump when the first case fades in.
  if (shuffled.length === 0) {
    return <section className="vigil vigil-placeholder" aria-hidden="true" />;
  }

  const c = shuffled[index % shuffled.length];
  const nameField = pickNameWithTransliteration(
    c.victim_name_ar,
    c.victim_name_he,
    c.victim_name_en ?? null,
    c.name_transliterations,
    lang,
  );
  const name = nameField.value;
  const city = pickLangField(c.city_ar, c.city_he, lang);

  const metaParts: React.ReactNode[] = [];
  if (c.victim_age !== null) {
    metaParts.push(
      <span key="age">
        {c.victim_age} {t(lang, "case.years_old")}
      </span>,
    );
  }
  if (city !== "—") {
    metaParts.push(<span key="city">{city}</span>);
  }
  if (c.incident_date) {
    metaParts.push(<span key="date">{formatDate(c.incident_date, lang)}</span>);
  }

  const showProgress = shuffled.length > 1;

  return (
    <section
      className={`vigil${isPaused ? " is-paused" : ""}`}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      <div className="wrap">
        <div className="vigil-eyebrow">{t(lang, "vigil.eyebrow")}</div>
        {/* Keyed wrapper restarts the CSS enter animation on every advance. */}
        <div className="vigil-stage" key={`${c.run_id}-${c.case_index}`}>
          <h1 className="vigil-name">{name}</h1>
          {metaParts.length > 0 && (
            <div className="vigil-meta">
              {metaParts.map((part, i) => (
                <Fragment key={i}>
                  {part}
                  {i < metaParts.length - 1 && (
                    <span className="vigil-sep" aria-hidden="true">·</span>
                  )}
                </Fragment>
              ))}
            </div>
          )}
          <Link
            href={`/cases/${c.run_id}/${c.case_index}`}
            className="vigil-link"
          >
            <span>{t(lang, "vigil.read_case")}</span>
            <span className="vigil-arrow" aria-hidden="true">←</span>
          </Link>
        </div>
        {showProgress && (
          <div className="vigil-progress" aria-hidden="true">
            <div
              className="vigil-progress-bar"
              key={`bar-${index}`}
              style={{ animationDuration: `${ROTATE_MS}ms` }}
            />
          </div>
        )}
      </div>
    </section>
  );
}
