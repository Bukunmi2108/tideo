import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobResponse, Manifest } from "./api";

vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  apiBase: () => "https://api.example.test",
  getJob: vi.fn(),
  getManifest: vi.fn(),
  postCancel: vi.fn(),
  postTranscode: vi.fn(),
}));
vi.mock("./live", () => ({ watch: vi.fn(() => () => {}) }));
vi.mock("./player", () => ({
  mountPlayer: vi.fn(() => ({ destroy: vi.fn() })),
}));
vi.mock("./sprite", () => ({
  loadStoryboard: vi.fn(async () => null),
  spriteUrl: vi.fn(() => "https://api.example.test/jobs/j1/sprite"),
}));

import { getJob, getManifest, postCancel, postTranscode } from "./api";
import { mount } from "./job";
import { watch, type WatchHandlers } from "./live";

const getJobMock = getJob as unknown as ReturnType<typeof vi.fn>;
const getManifestMock = getManifest as unknown as ReturnType<typeof vi.fn>;
const postCancelMock = postCancel as unknown as ReturnType<typeof vi.fn>;
const postTranscodeMock = postTranscode as unknown as ReturnType<typeof vi.fn>;
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
  postCancelMock.mockResolvedValue({ job_id: "j1", status: "cancelled" });
  postTranscodeMock.mockResolvedValue({ job_id: "j1", status: "queued" });
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

describe("inspect and choose", () => {
  it("presents friendly source details and visible decision guidance", async () => {
    getJobMock.mockResolvedValue({
      job_id: "j1",
      status: "awaiting_choice",
      source_filename: "conference-recording.webm",
      recommended_presets: ["720p", "480p", "360p"],
      web_safe: false,
      web_safe_reason:
        "video vp9 (need h264); audio opus (need aac); container matroska,webm (need mp4/mov)",
      source: {
        container: "matroska,webm",
        video_codec: "vp9",
        audio_codec: "opus",
        width: 1280,
        height: 720,
        duration: 120,
        bitrate: 2_400_000,
        fps: 30,
        has_audio: true,
        video_streams: 1,
        audio_streams: 1,
      },
    } satisfies JobResponse);

    teardown = mount(root, new URLSearchParams("id=j1"));
    await vi.waitFor(() =>
      expect(root.querySelector(".source-readiness")).toBeTruthy(),
    );

    expect(root.textContent).toContain("WebM");
    expect(root.textContent).toContain("VP9");
    expect(root.textContent).toContain("Opus");
    expect(root.textContent).not.toContain("matroska,webm");
    expect(root.querySelector(".source-readiness")?.textContent).toContain(
      "needs H.264 video, AAC audio, and an MP4 or MOV container",
    );
    expect(root.querySelector(".captions-copy")?.textContent).toContain(
      "WebVTT caption track from the source audio",
    );
    expect(root.querySelector(".picker-reason")?.textContent).toBe(
      "Source is 720p. Tideo will not upscale it.",
    );
    expect(root.querySelector("#estimate")?.textContent).toBe(
      "Rough estimate: about 2 minutes",
    );
  });

  it("explains why captions are unavailable when the source has no audio", async () => {
    getJobMock.mockResolvedValue({
      job_id: "j1",
      status: "awaiting_choice",
      source_filename: "silent.mp4",
      recommended_presets: ["360p"],
      web_safe: true,
      source: {
        container: "mov,mp4,m4a,3gp,3g2,mj2",
        video_codec: "h264",
        audio_codec: null,
        width: 640,
        height: 360,
        duration: 30,
        bitrate: 800_000,
        fps: 30,
        has_audio: false,
        video_streams: 1,
        audio_streams: 0,
      },
    } satisfies JobResponse);

    teardown = mount(root, new URLSearchParams("id=j1"));
    await vi.waitFor(() =>
      expect(root.querySelector("#captions-toggle")).toBeTruthy(),
    );

    expect(
      (root.querySelector("#captions-toggle") as HTMLInputElement).disabled,
    ).toBe(true);
    expect(root.querySelector(".captions-copy")?.textContent).toContain(
      "No audio track",
    );
  });
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

  it("identifies the job, exposes semantic progress, and recovers the live notice", async () => {
    getJobMock.mockResolvedValue({
      job_id: "j1",
      status: "transcoding",
      source_filename: "lesson.mp4",
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
      expect(root.querySelector(".progress-bar-track")).toBeTruthy(),
    );

    expect(root.querySelector(".processing-phase")?.textContent).toBe(
      "Transcoding",
    );
    expect(root.querySelector("h1")?.textContent).toBe("lesson.mp4");
    expect(
      root.querySelector<HTMLAnchorElement>(".processing-guidance a")
        ?.pathname,
    ).toBe("/history");
    expect(root.querySelector(".progress-bar-track")?.getAttribute("role")).toBe(
      "progressbar",
    );
    expect(
      root.querySelector(".progress-bar-track")?.getAttribute("aria-valuenow"),
    ).toBe("42");

    handlers!.onMode("polling");
    expect(root.querySelector(".connection-notice")?.textContent).toContain(
      "Live updates paused. Checking automatically.",
    );
    expect(root.querySelector(".connection-notice")?.getAttribute("role")).toBe(
      "status",
    );

    handlers!.onMode("live");
    expect(root.querySelector(".connection-notice")).toBeNull();

    handlers!.onProgress({
      type: "progress",
      preset: "720p",
      percent: 100,
    });
    expect(
      root.querySelector(".progress-bar-track")?.getAttribute("aria-valuenow"),
    ).toBe("100");
    expect(root.querySelector(".bar-row")?.classList.contains("is-complete")).toBe(
      true,
    );
  });

  it("confirms cancellation once and moves immediately to the cancelled state", async () => {
    getJobMock.mockResolvedValue({
      job_id: "j1",
      status: "transcoding",
      source_filename: "lesson.mp4",
      presets: ["720p"],
      progress: { "720p": 42 },
    } satisfies JobResponse);
    let finishCancel: ((value: { job_id: string; status: string }) => void) | undefined;
    postCancelMock.mockReturnValue(
      new Promise((resolve) => {
        finishCancel = resolve;
      }),
    );

    teardown = mount(root, new URLSearchParams("id=j1"));
    await vi.waitFor(() => expect(root.querySelector("#cancel-btn")).toBeTruthy());
    (root.querySelector("#cancel-btn") as HTMLButtonElement).click();

    expect(root.querySelector(".cancel-confirm h2")?.textContent).toBe(
      "Cancel transcoding?",
    );
    expect(root.querySelector(".cancel-confirm")?.textContent).toContain(
      "Completed temporary work may be discarded.",
    );
    const confirm = root.querySelector(
      "#cancel-confirm-btn",
    ) as HTMLButtonElement;
    confirm.click();
    confirm.click();

    expect(postCancelMock).toHaveBeenCalledTimes(1);
    expect(
      root.querySelector<HTMLButtonElement>("#cancel-confirm-btn")?.disabled,
    ).toBe(true);
    expect(document.activeElement).toBe(root.querySelector(".cancel-confirm"));
    expect(root.querySelector("#cancel-confirm-btn")?.textContent).toContain(
      "Cancelling",
    );

    finishCancel!({ job_id: "j1", status: "cancelled" });
    await vi.waitFor(() =>
      expect(root.querySelector("h1")?.textContent).toBe("Job cancelled"),
    );
  });
});

describe("terminal recovery", () => {
  it("maps backend diagnostics to a useful inspect-failure recovery", async () => {
    getJobMock.mockResolvedValue({
      job_id: "j1",
      status: "failed",
      source_filename: "broken.mp4",
      error: {
        code: "SOURCE_CORRUPT",
        message: "moov atom not found /srv/private/input",
        stage: "inspect",
        retryable: false,
      },
    } satisfies JobResponse);

    teardown = mount(root, new URLSearchParams("id=j1"));
    await vi.waitFor(() =>
      expect(root.querySelector("h1")?.textContent).toBe(
        "Couldn’t inspect this video",
      ),
    );

    expect(root.textContent).toContain("appears damaged or incomplete");
    expect(root.textContent).not.toContain("moov atom");
    expect(root.textContent).not.toContain("SOURCE_CORRUPT");
    expect(document.activeElement).toBe(root.querySelector("h1"));
    expect(
      root.querySelector('.terminal-actions a[href="/upload"]')?.textContent,
    ).toBe("Upload video");
    expect(
      root.querySelector('.terminal-actions a[href="/history"]')?.textContent,
    ).toBe("My videos");
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
