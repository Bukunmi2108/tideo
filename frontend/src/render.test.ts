import { describe, it, expect } from "vitest"
import { humanDuration, humanBytes, humanBitrate, relativeTime, expiresIn } from "./render"

const NOW = Date.parse("2026-06-17T12:00:00Z")

describe("relativeTime", () => {
  it("buckets recent times", () => {
    expect(relativeTime("2026-06-17T11:59:30Z", NOW)).toBe("just now")
    expect(relativeTime("2026-06-17T11:30:00Z", NOW)).toBe("30m ago")
    expect(relativeTime("2026-06-17T09:00:00Z", NOW)).toBe("3h ago")
    expect(relativeTime("2026-06-15T12:00:00Z", NOW)).toBe("2d ago")
  })
  it("falls back to a date past a week", () => {
    expect(relativeTime("2026-05-01T12:00:00Z", NOW)).toMatch(/May/)
  })
  it("returns empty for null/garbage", () => {
    expect(relativeTime(null, NOW)).toBe("")
    expect(relativeTime("not-a-date", NOW)).toBe("")
  })
})

describe("expiresIn", () => {
  it("counts down in days and hours", () => {
    expect(expiresIn("2026-06-19T12:00:00Z", NOW)).toBe("expires in 2d")
    expect(expiresIn("2026-06-17T17:00:00Z", NOW)).toBe("expires in 5h")
  })
  it("says soon under an hour and expired in the past", () => {
    expect(expiresIn("2026-06-17T12:30:00Z", NOW)).toBe("expires soon")
    expect(expiresIn("2026-06-16T12:00:00Z", NOW)).toBe("expired")
  })
  it("returns empty for null", () => {
    expect(expiresIn(null, NOW)).toBe("")
  })
})

describe("humanDuration", () => {
  it("formats m:ss and h:mm:ss", () => {
    expect(humanDuration(0)).toBe("0:00")
    expect(humanDuration(65)).toBe("1:05")
    expect(humanDuration(600)).toBe("10:00")
    expect(humanDuration(3725)).toBe("1:02:05")
  })

  it("guards non-finite input (player feeds NaN before metadata loads)", () => {
    expect(humanDuration(NaN)).toBe("0:00")
    expect(humanDuration(Infinity)).toBe("0:00")
  })

  it("never goes negative", () => {
    expect(humanDuration(-5)).toBe("0:00")
  })
})

describe("humanBitrate", () => {
  it("kbps below 1 Mbps, Mbps at/above", () => {
    expect(humanBitrate(800_000)).toBe("800 kbps")
    expect(humanBitrate(1_000_000)).toBe("1.0 Mbps")
    expect(humanBitrate(3_600_000)).toBe("3.6 Mbps")
  })

  it("renders a dash for null/zero", () => {
    expect(humanBitrate(null)).toBe("—")
    expect(humanBitrate(0)).toBe("—")
  })
})

describe("humanBytes", () => {
  it("scales units", () => {
    expect(humanBytes(512)).toBe("512 B")
    expect(humanBytes(2048)).toBe("2.0 KB")
    expect(humanBytes(5 * 1024 ** 2)).toBe("5.0 MB")
  })
})
