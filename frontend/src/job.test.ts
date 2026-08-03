import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobResponse, Manifest } from "./api";

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  apiBase: () => "https://api.example.test",
  getJob: vi.fn(),
  getManifest: vi.fn(),
}));
vi.mock("./live", () => ({ watch: vi.fn(() => () => {}) }));
vi.mock("./player", () => ({
  mountPlayer: vi.fn(() => ({ destroy: vi.fn() })),
}));
vi.mock("./sprite", () => ({
  loadStoryboard: vi.fn(async () => null),
  spriteUrl: vi.fn(() => "https://api.example.test/jobs/j1/sprite"),
}));

import { getJob, getManifest } from "./api";
import { mount } from "./job";
import { watch, type WatchHandlers } from "./live";

const getJobMock = getJob as unknown as ReturnType<typeof vi.fn>;
const getManifestMock = getManifest as unknown as ReturnType<typeof vi.fn>;
const watchMock = vi.mocked(watch);

const DONE_JOB: JobResponse = {
  job_id: "j1",
  status: "done",
  source_filename: "clip.mp4",
  results: {
    playlist: "/jobs/j1/playlist",
    web_mp4: "/jobs/j1/file",
    poster: "/jobs/j1/poster",
    sprite: "/jobs/j1/sprite",
    player: "/jobs/j1/player",
    presets: ["720p"],
    duration: 60,
  },
};

const MANIFEST: Manifest = {
  job_id: "j1",
  duration: 60,
  renditions: [],
  web_remuxed: true,
  storyboard: null,
  created_at: null,
};

let root: HTMLElement;
let teardown: () => void;

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
  getJobMock.mockResolvedValue(DONE_JOB);
  getManifestMock.mockResolvedValue(MANIFEST);
  watchMock.mockImplementation(() => () => {});
});

afterEach(() => {
  teardown?.();
  root.remove();
  Object.defineProperty(navigator, "share", {
    configurable: true,
    value: undefined,
  });
  vi.clearAllMocks();
});

describe("transcoding progress", () => {
  it("renders the initial progress and visually advances on live updates", async () => {
    getJobMock.mockResolvedValue({
      job_id: "j1",
      status: "transcoding",
      source_filename: "clip.mp4",
      presets: ["720p"],
      progress: { "720p": 42 },
    } satisfies JobResponse);
    let handlers: WatchHandlers | undefined;
    watchMock.mockImplementation((_jobId, nextHandlers) => {
      handlers = nextHandlers;
      return () => {};
    });

    teardown = mount(root, new URLSearchParams("id=j1"));
    await vi.waitFor(() =>
      expect(root.querySelector(".progress-bar-fill")).toBeTruthy(),
    );

    const fill = root.querySelector<HTMLElement>(".progress-bar-fill")!;
    const label = root.querySelector<HTMLElement>(".bar-pct")!;
    expect(label.textContent).toBe("42%");
    expect(fill.style.transform).toBe("scaleX(0.42)");

    handlers!.onProgress({
      type: "progress",
      preset: "720p",
      percent: 68,
    });

    expect(label.textContent).toBe("68%");
    expect(fill.style.transform).toBe("scaleX(0.68)");
  });
});

describe("completed-job sharing", () => {
  it("shares the standalone player URL through the native share sheet", async () => {
    const share = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: share,
    });

    teardown = mount(root, new URLSearchParams("id=j1"));
    await vi.waitFor(() =>
      expect(root.querySelector("#share-player")).toBeTruthy(),
    );
    (root.querySelector("#share-player") as HTMLButtonElement).click();

    await vi.waitFor(() =>
      expect(share).toHaveBeenCalledWith({
        title: "clip.mp4",
        url: "https://api.example.test/jobs/j1/player",
      }),
    );
  });

  it("copies the standalone player URL when native sharing is unavailable", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    teardown = mount(root, new URLSearchParams("id=j1"));
    await vi.waitFor(() =>
      expect(root.querySelector("#share-player")).toBeTruthy(),
    );
    const button = root.querySelector("#share-player") as HTMLButtonElement;
    button.click();

    await vi.waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        "https://api.example.test/jobs/j1/player",
      ),
    );
    expect(button.textContent).toBe("Link copied!");
  });
});
