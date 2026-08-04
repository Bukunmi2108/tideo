import { afterEach, describe, expect, it, vi } from "vitest";

const playerMock = vi.hoisted(() => {
  const handle = {
    destroy: vi.fn(),
    play: vi.fn().mockResolvedValue(undefined),
    reload: vi.fn(),
    selectQuality: vi.fn().mockReturnValue(true),
  };
  return {
    handle,
    mount: vi.fn((root: HTMLElement) => {
      root.innerHTML = '<video class="player-video" tabindex="0"></video>';
      return handle;
    }),
  };
});

vi.mock("./player", () => ({ mountPlayer: playerMock.mount }));

import { mount } from "./landing";

afterEach(() => vi.clearAllMocks());

describe("landing mount", () => {
  it("renders an immediate, privacy-safe product proof without backend data", () => {
    const root = document.createElement("div");
    const teardown = mount(root);

    expect(playerMock.mount).toHaveBeenCalledWith(
      root.querySelector("#demo-player"),
      expect.objectContaining({
        playlist: "/demo/sintel/master.m3u8",
        poster: "/demo/sintel-cinematic-poster.webp",
        spriteUrl: "/demo/sintel-cinematic-storyboard.webp",
      }),
    );
    expect(root.querySelector(".lp-proof-meta")?.textContent).toContain("Captions included");
    expect(root.querySelectorAll("[data-demo-quality]")).toHaveLength(3);
    expect(root.querySelector(".lp-proof-storyboard")).toBeNull();
    expect(root.querySelector(".lp-proof")?.textContent).not.toContain("CC ready");
    expect(root.querySelector(".lp-proof-credit")?.textContent).toContain("Blender Foundation");
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

  it("starts the checked-in demo from the Watch demo action", () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    const teardown = mount(root);

    root.querySelector<HTMLAnchorElement>("#watch-demo")?.click();

    expect(playerMock.handle.play).toHaveBeenCalledOnce();
    expect(document.activeElement).toBe(root.querySelector("#demo-player video"));
    teardown();
    root.remove();
  });

  it("switches the adaptive demo from the rendition buttons", () => {
    const root = document.createElement("div");
    const teardown = mount(root);
    const quality = root.querySelector<HTMLButtonElement>('[data-demo-quality="360"]')!;

    quality.click();

    expect(playerMock.handle.selectQuality).toHaveBeenCalledWith(360);
    expect(quality.getAttribute("aria-pressed")).toBe("true");
    teardown();
  });
});
