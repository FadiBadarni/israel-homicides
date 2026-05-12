import { fetchMemorial } from "@/lib/api";
import { MemorialMap } from "@/components/memorial-map";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  let memorial;
  try {
    memorial = await fetchMemorial();
  } catch {
    memorial = {
      run_id: null,
      year_range: { from: null, to: null },
      total_deaths: 0,
      unresolved_count: 0,
      localities: [],
    };
  }

  return <MemorialMap memorial={memorial} />;
}
