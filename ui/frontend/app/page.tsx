"use client";

import { useEffect, useState } from "react";
import { fetchMemorial, type MemorialResponse } from "@/lib/api";
import { MemorialMap } from "@/components/memorial-map";

const EMPTY: MemorialResponse = {
  run_id: null,
  year_range: { from: null, to: null },
  total_deaths: 0,
  unresolved_count: 0,
  localities: [],
};

export default function HomePage() {
  const [memorial, setMemorial] = useState<MemorialResponse | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchMemorial()
      .then((m) => {
        if (!alive) return;
        setMemorial(m);
        setLoadError(false);
      })
      .catch(() => {
        if (!alive) return;
        setMemorial(EMPTY);
        setLoadError(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!memorial) {
    return <div className="w-full h-screen" style={{ backgroundColor: "#f5f1ea" }} />;
  }

  return <MemorialMap memorial={memorial} loadError={loadError} />;
}
