import { describe, expect, it } from "vitest";
import { icon } from "./icons";

describe("icon", () => {
  it("renders official assets as decorative, non-focusable SVGs", () => {
    document.body.innerHTML = icon("upload");
    const svg = document.querySelector("svg");

    expect(svg?.classList.contains("icon--upload")).toBe(true);
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    expect(svg?.getAttribute("focusable")).toBe("false");
    expect(svg?.querySelector("path")).not.toBeNull();
  });
});
