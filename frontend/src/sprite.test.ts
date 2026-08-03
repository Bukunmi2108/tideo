import { afterEach, describe, expect, it, vi } from "vitest";
import type { Storyboard } from "./api";
import { playLoop, tileForFraction } from "./sprite";

const storyboard: Storyboard = {
  url: "sprite.jpg",
  tiles: 10,
  cols: 5,
  rows: 2,
  tile_w: 160,
  tile_h: 90,
  interval: 2,
};

afterEach(() => vi.useRealTimers());

describe("sprite helpers", () => {
  it("clamps fractions to valid storyboard tiles", () => {
    expect(tileForFraction(storyboard, -1)).toBe(0);
    expect(tileForFraction(storyboard, 0.5)).toBe(5);
    expect(tileForFraction(storyboard, 1)).toBe(9);
  });

  it("stops hover animation and resets to the first tile", () => {
    vi.useFakeTimers();
    const element = document.createElement("div");
    const loop = playLoop(element, storyboard, 5);
    vi.advanceTimersByTime(400);
    expect(element.style.backgroundPosition).not.toBe("0% 0%");

    loop.stop();
    const reset = element.style.backgroundPosition;
    vi.advanceTimersByTime(1000);
    expect(element.style.backgroundPosition).toBe(reset);
    expect(element.style.backgroundPosition).toBe("0% 0%");
  });
});
