import { describe, it, expect } from "vitest";
import {
  buildPicker,
  estimateSeconds,
  LADDER,
  ESTIMATE_RATIOS,
} from "./presets";

describe("buildPicker", () => {
  it("pre-checks recommended rungs and disables the rest with a reason", () => {
    const rows = buildPicker(["480p", "360p"], 480);
    const byPreset = Object.fromEntries(rows.map((r) => [r.preset, r]));

    expect(byPreset["480p"].available).toBe(true);
    expect(byPreset["480p"].checked).toBe(true);
    expect(byPreset["480p"].reason).toBeNull();

    expect(byPreset["1080p"].available).toBe(false);
    expect(byPreset["1080p"].checked).toBe(false);
    expect(byPreset["1080p"].reason).toBe("source is 480p — no upscale");
  });

  it("returns the full ladder in catalog (highest-first) order", () => {
    const rows = buildPicker(["1080p", "720p", "480p", "360p"], 1080);
    expect(rows.map((r) => r.preset)).toEqual([
      "1080p",
      "720p",
      "480p",
      "360p",
    ]);
    expect(rows.every((r) => r.available && r.checked)).toBe(true);
  });

  it("never marks a non-recommended rung available even if heights would allow it", () => {
    // the backend recommendation is the sole authority
    const rows = buildPicker(["720p"], 1080);
    expect(rows.filter((r) => r.available).map((r) => r.preset)).toEqual([
      "720p",
    ]);
  });

  it("covers every catalog rung", () => {
    const rows = buildPicker([], 720);
    expect(rows).toHaveLength(LADDER.length);
    expect(rows.every((r) => !r.available && r.reason !== null)).toBe(true);
  });
});

describe("estimateSeconds", () => {
  it("sums per-preset ratios against the source duration", () => {
    const out = estimateSeconds(["720p", "480p"], 100);
    expect(out).toBeCloseTo(
      100 * (ESTIMATE_RATIOS["720p"] + ESTIMATE_RATIOS["480p"]),
    );
  });

  it("is zero for no presets", () => {
    expect(estimateSeconds([], 100)).toBe(0);
  });

  it("falls back to a conservative ratio for an unknown preset", () => {
    expect(estimateSeconds(["4320p"], 100)).toBeCloseTo(50);
  });
});
