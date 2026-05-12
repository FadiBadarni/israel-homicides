"use client";

import { useEffect, useMemo, useState } from "react";
import type { Locality, MemorialResponse } from "@/lib/api";
import { BloomCard } from "./bloom-card";
import { DeathCount } from "./death-count";
import { IsraelMap } from "./israel-map";
import { YearScrubber } from "./year-scrubber";

interface MemorialMapProps {
  memorial: MemorialResponse;
  loadError?: boolean;
}

function slugify(city: string): string {
  return city.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export function MemorialMap({ memorial, loadError }: MemorialMapProps) {
  const [selectedLocality, setSelectedLocality] = useState<Locality | null>(null);
  const [selectedCaseIndex, setSelectedCaseIndex] = useState<number | null>(null);

  const yearMin = memorial.year_range.from ?? new Date().getFullYear();
  const yearMax = memorial.year_range.to ?? new Date().getFullYear();
  const [yearFrom, setYearFrom] = useState(yearMin);
  const [yearTo, setYearTo] = useState(yearMax);

  const filteredLocalities = useMemo(() => {
    return memorial.localities
      .map((loc) => {
        const deaths = loc.deaths.filter((d) => {
          if (!d.incident_date) return true;
          const y = Number(d.incident_date.slice(0, 4));
          return y >= yearFrom && y <= yearTo;
        });
        return { ...loc, deaths, death_count: deaths.length };
      })
      .filter((l) => l.death_count > 0);
  }, [memorial, yearFrom, yearTo]);

  // Close the bloom card if its locality drops out of the filter
  useEffect(() => {
    if (
      selectedLocality &&
      !filteredLocalities.find((l) => l.city === selectedLocality.city)
    ) {
      setSelectedLocality(null);
      setSelectedCaseIndex(null);
    }
  }, [filteredLocalities, selectedLocality]);

  // Read URL once on mount
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const slug = params.get("locality");
    if (!slug) return;
    const loc = memorial.localities.find((l) => slugify(l.city) === slug);
    if (!loc) return;
    setSelectedLocality(loc);
    const caseStr = params.get("case");
    if (caseStr) {
      const idx = Number(caseStr);
      if (!isNaN(idx)) setSelectedCaseIndex(idx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Write URL on selection change
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (selectedLocality) {
      params.set("locality", slugify(selectedLocality.city));
      if (selectedCaseIndex !== null) {
        params.set("case", String(selectedCaseIndex));
      } else {
        params.delete("case");
      }
    } else {
      params.delete("locality");
      params.delete("case");
    }
    const qs = params.toString();
    const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
    window.history.replaceState(null, "", url);
  }, [selectedLocality, selectedCaseIndex]);

  const visibleCount = filteredLocalities.reduce((s, l) => s + l.death_count, 0);

  const handleSelectCity = (city: string) => {
    const loc = filteredLocalities.find((l) => l.city === city);
    if (!loc) return;
    setSelectedLocality(loc);
    setSelectedCaseIndex(null);
  };

  return (
    <div
      className="flex h-screen w-full"
      style={{ backgroundColor: "#f5f1ea" }}
      onClick={() => {
        setSelectedLocality(null);
        setSelectedCaseIndex(null);
      }}
    >
      <aside className="w-[360px] flex-shrink-0 relative px-4 py-8">
        <div className="absolute top-3 left-4 z-10 text-xs font-medium text-neutral-700 tracking-wide">
          Crime Pipeline — Memorial
        </div>
        <div className="w-full h-full">
          <IsraelMap
            localities={filteredLocalities}
            selectedCity={selectedLocality?.city ?? null}
            onSelect={handleSelectCity}
          />
        </div>
      </aside>

      <main
        className="flex-1 relative overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {loadError && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 text-xs text-neutral-500 bg-white/80 backdrop-blur px-2 py-1 rounded">
            Unable to load memorial data.
          </div>
        )}

        {selectedLocality ? (
          <div className="h-full overflow-y-auto px-6 py-8">
            <BloomCard
              locality={selectedLocality}
              initialCaseIndex={selectedCaseIndex}
              onClose={() => {
                setSelectedLocality(null);
                setSelectedCaseIndex(null);
              }}
              onSelectCase={setSelectedCaseIndex}
            />
          </div>
        ) : (
          <div className="h-full flex items-center justify-center text-xs text-neutral-400">
            Click a locality to read the names.
          </div>
        )}

        <YearScrubber
          min={yearMin}
          max={yearMax}
          from={yearFrom}
          to={yearTo}
          onChange={(f, t) => {
            setYearFrom(f);
            setYearTo(t);
          }}
        />

        <DeathCount count={visibleCount} />
      </main>
    </div>
  );
}
