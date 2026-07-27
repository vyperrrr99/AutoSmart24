export interface RunOut {
  id: number;
  brand: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  listings_seen: number;
  new_listings: number;
  price_changes: number;
  sold_detected: number;
  errors_count: number;
  phase: string | null;
  search_finished_at: string | null;
  search_total: number | null;
  detail_total: number | null;
  detail_enriched: number;
}

export interface EventOut {
  id: number;
  run_id: number | null;
  brand: string | null;
  level: string;
  message: string;
  url: string | null;
  created_at: string;
}

export interface BrandStatusOut {
  make_id: number;
  brand: string;
  slug: string;
  paused: boolean;
  year_from_years: number | null;
  schedule_day_of_week: string | null;
  schedule_hour: number;
  schedule_minute: number;
  last_run: RunOut | null;
}

export interface BrandCatalogEntryOut {
  make_id: number;
  display_name: string;
  slug: string;
}

export interface BrandDefaultsPatch {
  year_from_years?: number | null;
  schedule_day_of_week?: string | null;
  schedule_hour?: number;
  schedule_minute?: number;
}

// Payload contract for POST /brands/bulk: unlike BrandDefaultsPatch (used for
// PATCH, where a subset of fields is genuinely allowed), the backend requires
// schedule_hour and schedule_minute to be present for a bulk add.
export interface BrandBulkAddPatch {
  year_from_years: number | null;
  schedule_day_of_week: string | null;
  schedule_hour: number;
  schedule_minute: number;
}

export interface QueueCurrent {
  slug: string;
  brand: string;
  phase: string | null;
  done: number;
  total: number | null;
  percent: number | null;
  eta_seconds: number | null;
  eta_is_fallback: boolean;
  started_at: string;
}

export interface QueuePending {
  slug: string;
  brand: string;
  position: number;
  eta_seconds: number | null;
}

export interface QueueOut {
  halted: boolean;
  halted_reason: string | null;
  halted_at: string | null;
  current: QueueCurrent | null;
  pending: QueuePending[];
  total_eta_seconds: number | null;
}

export interface RunMetrics {
  run_id: number;
  started_at: string;
  status: string;
  search_seconds: number | null;
  search_items: number | null;
  search_rate_per_min: number | null;
  detail_seconds: number | null;
  detail_items: number | null;
  detail_rate_per_min: number | null;
}
