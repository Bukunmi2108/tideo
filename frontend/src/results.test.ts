import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobResults, Manifest } from "./api";

const playerHandle = vi.hoisted(() => ({ destroy: vi.fn(), reload: vi.fn() }));

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  apiBase: () => "https://api.example.test",
  getManifest: vi.fn(async () => ({
    job_id: "j1",
    duration: 60,
    renditions: [],
    web_remuxed: true,
    storyboard: null,
    created_at: null,
  } satisfies Manifest)),
}));
vi.mock("./player", () => ({
  mountPlayer: vi.fn(() => playerHandle),
}));
vi.mock("./sprite", () => ({
  loadStoryboard: vi.fn(async () => null),
  spriteUrl: vi.fn(() => "https://api.example.test/jobs/j1/sprite"),
}));

import {
  mountCompletedResult,
  renderCompletedResult,
  type CompletedResultOptions,
} from "./results";

const results: JobResults = {
  playlist: "/jobs/j1/playlist",
  web_mp4: "/jobs/j1/file",
  poster: "/jobs/j1/poster",
  sprite: "/jobs/j1/sprite",
  player: "/jobs/j1/player",
  presets: ["720p"],
  duration: 60,
  subtitles: { status: "processing" },
};

const options: CompletedResultOptions = {
  jobId: "j1",
  title: "clip.mp4",
  results,
  expiresAt: "2026-06-24T11:01:00Z",
};

let root: HTMLElement;

beforeEach(() => {
  root = document.createElement("div");
  root.innerHTML = renderCompletedResult(options);
  document.body.appendChild(root);
  playerHandle.destroy.mockClear();
  playerHandle.reload.mockClear();
});

afterEach(() => {
  root.remove();
  vi.clearAllMocks();
});

describe("completed result", () => {
  it("prioritizes sharing and groups stream, download, captions, retention, and embed details", () => {
    const actions = Array.from(
      root.querySelectorAll<HTMLElement>(".watch-actions > *"),
    );
    expect(actions[0].textContent).toContain("Share video");
    expect(actions[0].classList.contains("btn-primary")).toBe(true);
    expect(root.textContent).toContain("Anyone with the link can watch");
    expect(root.textContent).toContain("Stream");
    expect(root.textContent).toContain("Download");
    expect(root.textContent).toContain("Captions");
    expect(root.textContent).toContain("Retention");
    expect(root.querySelector("details")?.textContent).toContain("Developer embed");
    expect(root.querySelector("time")?.dateTime).toBe(options.expiresAt);
  });

  it("announces clipboard success without relying on a transient button label", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const handle = mountCompletedResult(root, options);
    (root.querySelector("#copy-master") as HTMLButtonElement).click();

    await vi.waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    expect(root.querySelector(".copy-feedback")?.textContent).toContain(
      "Stream URL copied",
    );
    expect(root.querySelector(".copy-feedback")?.getAttribute("aria-live")).toBe(
      "polite",
    );
    handle.destroy();
  });

  it("updates caption status in place and refreshes the mounted stream", async () => {
    const handle = mountCompletedResult(root, options);
    await vi.waitFor(() => expect(root.querySelector(".player-mount")?.classList.contains("player--stage")).toBe(true));

    handle.updateSubtitles({ status: "ready", url: "/captions.vtt" });

    expect(root.querySelector(".caption-detail")?.textContent).toContain("Ready");
    expect(playerHandle.reload).toHaveBeenCalledOnce();
    handle.destroy();
    expect(playerHandle.destroy).toHaveBeenCalledOnce();
  });
});
