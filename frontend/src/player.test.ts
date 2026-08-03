import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hlsState = vi.hoisted(() => ({
  instances: [] as Array<Record<string, any>>,
  supported: true,
}));

vi.mock("hls.js", () => {
  class MockHls {
    static Events = {
      ERROR: "error",
      LEVEL_SWITCHED: "level-switched",
      MANIFEST_PARSED: "manifest-parsed",
      SUBTITLE_TRACKS_UPDATED: "subtitle-tracks-updated",
    };
    static ErrorTypes = {
      MEDIA_ERROR: "media-error",
      NETWORK_ERROR: "network-error",
    };
    static isSupported = () => hlsState.supported;

    currentLevel = -1;
    levels = [{ height: 480 }, { height: 720 }];
    subtitleDisplay = false;
    subtitleTrack = -1;
    subtitleTracks: Array<Record<string, unknown>> = [];
    handlers = new Map<string, (...args: any[]) => void>();
    loadSource = vi.fn();
    attachMedia = vi.fn();
    startLoad = vi.fn();
    recoverMediaError = vi.fn();
    destroy = vi.fn();

    constructor() {
      hlsState.instances.push(this);
    }

    on(event: string, handler: (...args: any[]) => void) {
      this.handlers.set(event, handler);
    }

    emit(event: string, data?: unknown) {
      this.handlers.get(event)?.(event, data);
    }
  }
  return { default: MockHls };
});

import { mountPlayer } from "./player";

let root: HTMLElement;

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
  hlsState.instances.length = 0;
  hlsState.supported = true;
});

afterEach(() => {
  root.remove();
  vi.restoreAllMocks();
});

describe("player", () => {
  it("starts with a poster and discoverable icon controls without autoplay", () => {
    const handle = mountPlayer(root, {
      playlist: "/master.m3u8",
      poster: "/poster.jpg",
    });

    const video = root.querySelector("video") as HTMLVideoElement;
    expect(video.autoplay).toBe(false);
    expect(video.getAttribute("poster")).toBe("/poster.jpg");
    expect(root.classList.contains("player--controls-visible")).toBe(true);
    expect(root.querySelector(".pl-play svg")).not.toBeNull();
    expect(root.querySelector(".pl-full svg")).not.toBeNull();
    expect(root.textContent).not.toContain("▶");
    expect(root.textContent).not.toContain("⛶");

    handle.destroy();
  });

  it("uses a native quality select that supports automatic and manual playback", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;
    hls.emit("manifest-parsed");

    const quality = root.querySelector(".pl-quality-select") as HTMLSelectElement;
    expect(Array.from(quality.options).map((option) => option.textContent)).toEqual([
      "Auto",
      "480p",
      "720p",
    ]);
    quality.value = "1";
    quality.dispatchEvent(new Event("change", { bubbles: true }));
    expect(hls.currentLevel).toBe(1);

    handle.destroy();
    expect(hls.destroy).toHaveBeenCalledOnce();
  });

  it("toggles captions and exposes the selected state to assistive technology", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;
    hls.subtitleTracks = [{}];
    hls.emit("subtitle-tracks-updated");

    const captions = root.querySelector(".pl-cc") as HTMLButtonElement;
    expect(captions.hidden).toBe(false);
    captions.click();
    expect(hls.subtitleTrack).toBe(0);
    expect(captions.getAttribute("aria-pressed")).toBe("true");

    handle.destroy();
  });

  it("supports familiar video keyboard controls from the player surface", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const video = root.querySelector("video") as HTMLVideoElement;
    const play = vi.spyOn(video, "play").mockResolvedValue();
    Object.defineProperty(video, "duration", { configurable: true, value: 60 });
    Object.defineProperty(video, "currentTime", { configurable: true, writable: true, value: 20 });

    video.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
    expect(play).toHaveBeenCalledOnce();
    video.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(video.currentTime).toBe(25);
    video.dispatchEvent(new KeyboardEvent("keydown", { key: "m", bubbles: true }));
    expect(video.muted).toBe(true);

    handle.destroy();
  });

  it("uses native HLS on Safari and removes the manual quality control", () => {
    hlsState.supported = false;
    vi.spyOn(HTMLMediaElement.prototype, "canPlayType").mockReturnValue("maybe");

    const handle = mountPlayer(root, { playlist: "/master.m3u8" });

    const video = root.querySelector("video") as HTMLVideoElement;
    expect(video.getAttribute("src")).toContain("/master.m3u8");
    expect((root.querySelector(".pl-quality") as HTMLElement).hidden).toBe(true);
    expect(hlsState.instances).toHaveLength(0);
    handle.destroy();
  });

  it("hides volume controls when the browser reports no audio track", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const video = root.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(video, "audioTracks", {
      configurable: true,
      value: { length: 0 },
    });

    video.dispatchEvent(new Event("loadedmetadata"));

    expect((root.querySelector(".pl-volume") as HTMLElement).hidden).toBe(true);
    handle.destroy();
  });

  it("surfaces an expired-stream recovery after bounded HLS retries", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;
    for (let attempt = 0; attempt < 4; attempt += 1)
      hls.emit("error", { fatal: true, type: "network-error" });

    expect(root.querySelector(".player-error")?.textContent).toContain(
      "expired",
    );
    expect(hls.startLoad).toHaveBeenCalledTimes(3);
    (root.querySelector(".player-retry") as HTMLButtonElement).click();
    expect(hlsState.instances).toHaveLength(2);

    handle.destroy();
  });

  it("requests fullscreen from its own player container", () => {
    const requestFullscreen = vi.fn(async () => undefined);
    Object.defineProperty(root, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });

    (root.querySelector(".pl-full") as HTMLButtonElement).click();

    expect(requestFullscreen).toHaveBeenCalledOnce();
    handle.destroy();
  });
});
