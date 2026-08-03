import { beforeEach, describe, expect, it } from "vitest";
import { applyRouteChrome, routeMeta } from "./router";

beforeEach(() => {
  document.head.innerHTML = '<meta name="description" content="old">';
  document.body.innerHTML = '<div id="app"><main><h1>Upload</h1></main></div>';
});

describe("route metadata", () => {
  it("provides production titles for every route", () => {
    expect(routeMeta("/").title).toBe("Tideo | Adaptive video, on demand");
    expect(routeMeta("/upload").title).toBe("Upload | Tideo");
    expect(routeMeta("/job").title).toBe("Video job | Tideo");
    expect(routeMeta("/history").title).toBe("My videos | Tideo");
    expect(routeMeta("/privacy").title).toBe("Privacy | Tideo");
    expect(routeMeta("/terms").title).toBe("Terms | Tideo");
    expect(routeMeta("/missing").title).toBe("Page not found | Tideo");
  });
});

describe("applyRouteChrome", () => {
  it("sets title, description, and the stable skip-link target", () => {
    const root = document.querySelector("#app") as HTMLElement;
    const meta = routeMeta("/upload");

    applyRouteChrome(root, meta, false);

    expect(document.title).toBe(meta.title);
    expect(document.querySelector('meta[name="description"]')?.getAttribute("content")).toBe(
      meta.description,
    );
    expect(root.querySelector("main")?.id).toBe("main-content");
  });

  it("moves focus and announces client-side navigation", () => {
    const root = document.querySelector("#app") as HTMLElement;

    applyRouteChrome(root, routeMeta("/upload"), true);

    expect(document.activeElement).toBe(root.querySelector("h1"));
    expect(document.querySelector('[aria-live="polite"]')?.textContent).toBe(
      "Upload | Tideo",
    );
  });
});
