const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export interface DeathSummary {
  case_index: number;
  run_id: string;
  victim_name: string | null;
  victim_name_he: string | null;
  victim_name_ar: string | null;
  victim_age: number | null;
  incident_date: string | null;
  confidence_score: number | null;
}

export interface Locality {
  city: string;
  city_he: string | null;
  city_ar: string | null;
  lat: number;
  lng: number;
  death_count: number;
  most_recent_incident_date: string | null;
  deaths: DeathSummary[];
}

export interface MemorialResponse {
  run_id: string | null;
  year_range: { from: number | null; to: number | null };
  total_deaths: number;
  unresolved_count: number;
  localities: Locality[];
}

export interface Source {
  url: string;
  domain?: string | null;
  source_name?: string | null;
  actual_publisher?: string | null;
  discovery_source?: string | null;
  language?: string | null;
  published_at: string | null;
  title?: string | null;
  role?: string | null;
  tier?: number | null;
}

export interface MediaItem {
  primary_url: string;
  type: string | null;
  is_evidence: boolean;
  caption: string | null;
}

export interface CaseDetail {
  case_index: number;
  run_id: string;
  victim_name: string | null;
  victim_name_he: string | null;
  victim_name_ar: string | null;
  victim_name_en: string | null;
  victim_age: number | null;
  victim_gender: string | null;
  incident_date: string | null;
  death_date: string | null;
  city: string | null;
  neighborhood: string | null;
  district: string | null;
  weapon_type: string | null;
  suspect_status: string | null;
  legal_status: string | null;
  case_narrative: string | null;
  sources: Source[];
  media_evidence: MediaItem[];
  conflicts?: Record<string, unknown> | null;
  conflict_map?: Record<string, unknown> | null;
}

export async function fetchMemorial(): Promise<MemorialResponse> {
  const res = await fetch(`${API_BASE}/api/memorial`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchCase(runId: string, caseIndex: number): Promise<CaseDetail> {
  const res = await fetch(`${API_BASE}/api/cases/${runId}/${caseIndex}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
