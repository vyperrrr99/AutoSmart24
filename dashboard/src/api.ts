import type { BrandBulkAddPatch, BrandCatalogEntryOut, BrandDefaultsPatch, BrandStatusOut, EventOut, RunOut } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Thrown by the request helpers below instead of a bare Error so callers can
// branch on the HTTP status code (e.g. to show a specific message for a 422)
// without re-parsing the error text.
export class ApiError extends Error {
  status: number;

  constructor(path: string, status: number) {
    super(`Request to ${path} failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new ApiError(path, response.status);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(path, response.status);
  }
  return response.json() as Promise<T>;
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(path, response.status);
  }
  return response.json() as Promise<T>;
}

async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "DELETE" });
  if (!response.ok) {
    throw new ApiError(path, response.status);
  }
  return response.json() as Promise<T>;
}

export function fetchBrands(): Promise<BrandStatusOut[]> {
  return getJson<BrandStatusOut[]>("/brands");
}

export function fetchBrandRuns(brandSlug: string): Promise<RunOut[]> {
  return getJson<RunOut[]>(`/brands/${brandSlug}/runs`);
}

export function fetchBrandEvents(brandSlug: string): Promise<EventOut[]> {
  return getJson<EventOut[]>(`/brands/${brandSlug}/events`);
}

export function pauseBrand(brandSlug: string): Promise<{ paused: boolean }> {
  return postJson(`/brands/${brandSlug}/pause`);
}

export function resumeBrand(brandSlug: string): Promise<{ paused: boolean }> {
  return postJson(`/brands/${brandSlug}/resume`);
}

export function runBrandNow(brandSlug: string): Promise<{ triggered: boolean }> {
  return postJson(`/brands/${brandSlug}/run-now`);
}

export function fetchBrandCatalog(): Promise<BrandCatalogEntryOut[]> {
  return getJson<BrandCatalogEntryOut[]>("/brand-catalog");
}

export function refreshBrandCatalog(): Promise<{ count: number }> {
  return postJson("/brand-catalog/refresh");
}

export function addBrands(makeIds: number[], defaults: BrandBulkAddPatch): Promise<BrandStatusOut[]> {
  return postJson("/brands/bulk", { make_ids: makeIds, ...defaults });
}

export function updateBrand(brandSlug: string, patch: BrandDefaultsPatch): Promise<BrandStatusOut> {
  return patchJson(`/brands/${brandSlug}`, patch);
}

export function applyDefaultsToAllBrands(patch: BrandDefaultsPatch): Promise<BrandStatusOut[]> {
  return patchJson("/brands/apply-defaults", patch);
}

export function removeBrand(brandSlug: string): Promise<{ deleted: boolean }> {
  return deleteJson(`/brands/${brandSlug}`);
}
