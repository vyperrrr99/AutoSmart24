import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fetchQueue, resumeQueue, fetchBrandMetrics } from "./api";

describe("api queue endpoints", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ halted: false }) })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("fetches the queue", async () => {
    const result = await fetchQueue();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/queue"));
    expect(result).toEqual({ halted: false });
  });

  it("posts to resume the queue", async () => {
    await resumeQueue();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/queue/resume"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("fetches brand metrics", async () => {
    await fetchBrandMetrics("fiat");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/brands/fiat/metrics"));
  });
});
