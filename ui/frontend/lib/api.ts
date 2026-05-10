const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface CaseSummary {
  case_index: number;
  run_id: string;
  victim_name: string | null;
  victim_name_ar: string | null;
  victim_name_he: string | null;
  victim_age: number | null;
  victim_gender: string | null;
  victim_outcome: "died" | "survived" | "critical" | "unknown" | null;
  incident_date: string | null;
  city: string | null;
  district: string | null;
  weapon_type: string | null;
  suspect_status: string | null;
  legal_status: string | null;
  confidence_score: number | null;
  review_status: string | null;
  source_count: number;
  flags: string[];
  media_count: number;
  canonical_case_id: string | null;
}

export interface CasesResponse {
  total: number;
  page: number;
  limit: number;
  pages: number;
  cases: CaseSummary[];
}

export interface Source {
  url: string;
  domain: string;
  published_at: string | null;
  title: string | null;
  role: string | null;
  tier: number | null;
}

export interface MediaItem {
  media_id: string | null;
  type: string | null;
  primary_url: string;
  mirror_urls: string[];
  sha256: string | null;
  phash: string | null;
  width: number | null;
  height: number | null;
  classifier_tier: string | null;
  confidence: number | null;
  classification_evidence: string[] | null;
  is_evidence: boolean;
  is_stock_photo: boolean;
  appearance_count: number;
  caption: string | null;
  alt_text: string | null;
  source_article_urls: string[];
}

export interface CaseDetail extends CaseSummary {
  victim_name_en: string | null;
  aliases: string[];
  neighborhood: string | null;
  region: string | null;
  place_type: string | null;
  death_date: string | null;
  suspect_description: string | null;
  suspect_age: number | null;
  case_narrative: string | null;
  sources: Source[];
  media: MediaItem[];
  media_evidence: MediaItem[];
  conflict_map: Record<string, unknown> | null;
  provenance: Record<string, unknown> | null;
}

export interface RunMeta {
  run_id: string;
  file: string;
  case_count: number;
  exported_at: string | null;
  stages: string[];
  non_fatal_excluded: number;
  confidence_avg: number;
}

export interface FiltersResponse {
  cities: string[];
  weapon_types: string[];
  outcomes: string[];
  review_statuses: string[];
  districts: string[];
}

export interface StatsResponse {
  total_cases: number;
  outcomes: Record<string, number>;
  top_cities: [string, number][];
  by_year: Record<string, number>;
}

export interface CasesParams {
  page?: number;
  limit?: number;
  city?: string;
  district?: string;
  outcome?: string;
  weapon_type?: string;
  search?: string;
  min_confidence?: number;
  review_status?: string;
  date_from?: string;
  date_to?: string;
  flagged?: boolean;
  named_only?: boolean;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

function buildQuery(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") {
      p.set(k, String(v));
    }
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function fetchCases(params: CasesParams = {}): Promise<CasesResponse> {
  const res = await fetch(`${API_BASE}/api/cases${buildQuery(params as Record<string, unknown>)}`, {
    cache: "no-store",
  });
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

export async function fetchRuns(): Promise<RunMeta[]> {
  const res = await fetch(`${API_BASE}/api/runs`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchFilters(): Promise<FiltersResponse> {
  const res = await fetch(`${API_BASE}/api/filters`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchStats(): Promise<StatsResponse> {
  const res = await fetch(`${API_BASE}/api/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export interface ReviewSide {
  victim_name: string | null;
  city: string | null;
  url: string | null;
}

export interface ReviewPair {
  a: ReviewSide;
  b: ReviewSide;
  jaro_score: number;
  cosine_score: number;
}

export interface ReviewPairsResponse {
  run_id: string | null;
  pairs: ReviewPair[];
}

export async function fetchReviewPairs(): Promise<ReviewPairsResponse> {
  const res = await fetch(`${API_BASE}/api/review-pairs`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
