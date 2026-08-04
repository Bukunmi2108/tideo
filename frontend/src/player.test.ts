import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hlsState = vi.hoisted(() => ({
  configs: [] as Array<Record<string, unknown>>,
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

    constructor(config: Record<string, unknown>) {
      hlsState.configs.push(config);
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
  hlsState.configs.length = 0;
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
    const poster = root.querySelector(".player-poster") as HTMLImageElement;
    expect(video.autoplay).toBe(false);
    expect(video.getAttribute("poster")).toBe("/poster.jpg");
    expect(poster.getAttribute("src")).toBe("/poster.jpg");
    expect(poster.hidden).toBe(false);
    expect(root.classList.contains("player--controls-visible")).toBe(true);
    expect(root.querySelector(".pl-play svg")).not.toBeNull();
    expect(root.querySelector(".pl-full svg")).not.toBeNull();
    expect(root.textContent).not.toContain("▶");
    expect(root.textContent).not.toContain("⛶");

    Object.defineProperty(video, "paused", { configurable: true, value: false });
    video.dispatchEvent(new Event("play"));
    expect(poster.hidden).toBe(true);

    handle.destroy();
  });

  it("uses a native quality select that supports automatic and manual playback", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;
    hls.emit("manifest-parsed");

    const quality = root.querySelector(".pl-quality-select") as HTMLSelectElement;
    const caret = root.querySelector(".pl-quality > .icon--caret");
    expect(Array.from(quality.options).map((option) => option.textContent)).toEqual([
      "Auto",
      "480p",
      "720p",
    ]);
    expect(caret?.getAttribute("aria-hidden")).toBe("true");
    quality.value = "1";
    quality.dispatchEvent(new Event("change", { bubbles: true }));
    expect(hls.currentLevel).toBe(1);

    quality.value = "-1";
    quality.dispatchEvent(new Event("change", { bubbles: true }));
    expect(hls.currentLevel).toBe(-1);

    handle.destroy();
    expect(hls.destroy).toHaveBeenCalledOnce();
  });

  it("defers media loading until explicit play and starts only once", async () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;
    expect(hlsState.configs[0]).toMatchObject({
      enableWorker: true,
      autoStartLoad: false,
      startLevel: -1,
      maxBufferLength: 20,
      maxMaxBufferLength: 30,
    });
    expect(hls.startLoad).not.toHaveBeenCalled();

    const video = root.querySelector("video") as HTMLVideoElement;
    let paused = true;
    Object.defineProperty(video, "paused", {
      configurable: true,
      get: () => paused,
    });
    vi.spyOn(video, "play").mockImplementation(async () => {
      paused = false;
      video.dispatchEvent(new Event("play"));
    });
    vi.spyOn(video, "pause").mockImplementation(() => {
      paused = true;
      video.dispatchEvent(new Event("pause"));
    });

    (root.querySelector(".pl-center-play") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(hls.startLoad).toHaveBeenCalledOnce());
    (root.querySelector(".pl-play") as HTMLButtonElement).click();
    (root.querySelector(".pl-play") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(video.play).toHaveBeenCalledTimes(2));
    expect(hls.startLoad).toHaveBeenCalledOnce();

    handle.destroy();
  });

  it("shows the active rendition while remaining in automatic mode", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;
    hls.emit("manifest-parsed");
    const quality = root.querySelector(".pl-quality-select") as HTMLSelectElement;

    hls.emit("level-switched", { level: 0 });
    expect(quality.value).toBe("-1");
    expect(quality.selectedOptions[0].textContent).toBe("Auto · 480p");

    quality.value = "1";
    quality.dispatchEvent(new Event("change", { bubbles: true }));
    hls.emit("level-switched", { level: 0 });
    expect(quality.value).toBe("1");
    expect(
      Array.from(quality.options).find((option) => option.value === "1")
        ?.textContent,
    ).toBe("720p");

    handle.destroy();
  });

  it("reloads an active transport from the current playback position", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;
    const video = root.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(video, "currentTime", {
      configurable: true,
      writable: true,
      value: 12,
    });
    vi.spyOn(video, "play").mockResolvedValue();

    (root.querySelector(".pl-center-play") as HTMLButtonElement).click();
    handle.reload();

    expect(hls.loadSource).toHaveBeenCalledTimes(2);
    expect(hls.startLoad).toHaveBeenNthCalledWith(2, 12);
    handle.destroy();
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
    expect(captions.getAttribute("aria-label")).toBe("Turn captions off");
    const status = root.querySelector(".pl-status") as HTMLElement;
    expect(status.hidden).toBe(false);
    expect(status.textContent).toBe(
      "Captions on — dialogue will appear when available.",
    );

    captions.click();
    expect(status.textContent).toBe("Captions off.");

    handle.destroy();
  });

  it("supports familiar video keyboard controls from the player surface", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;
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
    hls.subtitleTracks = [{}];
    hls.emit("subtitle-tracks-updated");
    video.dispatchEvent(new KeyboardEvent("keydown", { key: "c", bubbles: true }));
    expect(hls.subtitleTrack).toBe(0);
    expect(root.querySelector(".pl-status")?.textContent).toContain("Captions on");

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
    const video = root.querySelector("video") as HTMLVideoElement;
    vi.spyOn(video, "play").mockResolvedValue();
    (root.querySelector(".pl-center-play") as HTMLButtonElement).click();
    hls.startLoad.mockClear();
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

  it("retries a failed manifest without starting media before Play", () => {
    const handle = mountPlayer(root, { playlist: "/master.m3u8" });
    const hls = hlsState.instances[0] as any;

    hls.emit("error", { fatal: true, type: "network-error" });

    expect(hls.startLoad).not.toHaveBeenCalled();
    expect(hls.loadSource).toHaveBeenCalledTimes(2);
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
