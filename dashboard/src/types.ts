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
  brand: string;
  slug: string;
  paused: boolean;
  last_run: RunOut | null;
}
