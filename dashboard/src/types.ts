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
