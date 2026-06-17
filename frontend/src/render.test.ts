import { describe, it, expect } from "vitest"
import { humanDuration, humanBytes, humanBitrate } from "./render"

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
