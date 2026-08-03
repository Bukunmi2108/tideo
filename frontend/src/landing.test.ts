import { describe, expect, it } from "vitest";
import { mount } from "./landing";

describe("landing mount", () => {
  it("renders an immediate, privacy-safe product proof without backend data", () => {
    const root = document.createElement("div");
    const teardown = mount(root);

    const preview = root.querySelector<HTMLImageElement>(".lp-proof-poster");
    expect(preview?.getAttribute("src")).toBe("/demo/tideo-test-pattern-poster.webp");
    expect(preview?.getAttribute("alt")).toContain("synthetic test pattern");
    expect(root.querySelector(".lp-proof")?.textContent).toContain("7.5 Mbps");
    expect(root.querySelector(".lp-trust")?.textContent).toContain("this browser");
    expect(root.querySelector(".lp-trust")?.textContent).toContain("Sensitive material");

    teardown();
  });

  it("uses consistent actions and action-labelled workflow steps", () => {
    const root = document.createElement("div");
    const teardown = mount(root);

    expect(root.querySelector("h1")?.textContent).toBe("One upload. Every playback size.");
    expect(root.querySelector('.lp-actions a[href="/upload"]')?.textContent).toBe("Upload video");
    expect(root.querySelector('a[href="#demo-output"]')?.textContent).toBe("Watch demo");
    expect(
      Array.from(root.querySelectorAll(".lp-step-title"), (item) => item.textContent),
    ).toEqual(["Upload", "Inspect", "Choose outputs", "Encode", "Stream"]);
    expect(root.querySelector(".lp-step-n")).toBeNull();

    teardown();
  });
});
