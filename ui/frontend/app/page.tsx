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

  useEffect(() => {
    let alive = true;
    fetchMemorial()
      .then((m) => alive && setMemorial(m))
      .catch(() => alive && setMemorial(EMPTY));
    return () => {
      alive = false;
    };
  }, []);

  if (!memorial) {
    return (
      <div className="w-full h-screen flex items-center justify-center text-xs text-neutral-500">
        Loading memorial…
      </div>
    );
  }

  return <MemorialMap memorial={memorial} />;
}
