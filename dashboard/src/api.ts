import type { BrandStatusOut, EventOut, RunOut } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
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
