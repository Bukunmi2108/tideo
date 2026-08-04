import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type TestInfo } from "@playwright/test";
import { readFile } from "node:fs/promises";

export const JOB_ID = "browser-quality-gate";
const PLAYER_FIXTURE_ROOT = new URL("./fixtures/player/", import.meta.url);


export async function installPlayerMediaFixture(page: Page): Promise<void> {
  await page.route("**/e2e/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const relative = pathname.split("/e2e/", 2)[1] ?? "";
    if (!relative || relative.includes("..")) {
      await route.abort();
      return;
    }
    try {
      const body = await readFile(new URL(relative, PLAYER_FIXTURE_ROOT));
      const contentType = relative.endsWith(".m3u8")
        ? "application/vnd.apple.mpegurl"
        : relative.endsWith(".vtt")
          ? "text/vtt"
          : "video/mp2t";
      await route.fulfill({ status: 200, contentType, body });
    } catch {
      await route.fulfill({ status: 404, body: "fixture not found" });
    }
  });
}

const SOURCE = {
  container: "mov,mp4,m4a,3gp,3g2,mj2",
  video_codec: "h264",
  audio_codec: "aac",
  width: 1920,
  height: 1080,
  duration: 92,
  bitrate: 7_500_000,
  fps: 30,
  has_audio: true,
  video_streams: 1,
  audio_streams: 1,
};

export const AWAITING_JOB = {
  job_id: JOB_ID,
  status: "awaiting_choice",
  source_filename: "launch-film-final-v27-with-a-very-long-filename.mp4",
  source: SOURCE,
  recommended_presets: ["1080p", "720p", "480p"],
  web_safe: true,
  web_safe_reason: "H.264 video and AAC audio are ready for browser playback.",
  expires_at: "2030-01-02T12:00:00Z",
};

export const PROCESSING_JOB = {
  job_id: JOB_ID,
  status: "transcoding",
  source_filename: AWAITING_JOB.source_filename,
  source: SOURCE,
  presets: ["1080p", "720p", "480p"],
  progress: { "1080p": 68, "720p": 44, "480p": 100 },
  expires_at: "2030-01-02T12:00:00Z",
};

export const DONE_JOB = {
  job_id: JOB_ID,
  status: "done",
  source_filename: AWAITING_JOB.source_filename,
  source: SOURCE,
  presets: ["1080p", "720p", "480p"],
  progress: { "1080p": 100, "720p": 100, "480p": 100 },
  expires_at: "2030-01-02T12:00:00Z",
  results: {
    playlist: "/e2e/master.m3u8",
    web_mp4: "/e2e/video.mp4",
    poster: "/demo/tideo-test-pattern-poster.webp",
    sprite: "/demo/tideo-test-pattern-storyboard.webp",
    player: `/job?id=${JOB_ID}`,
    presets: ["1080p", "720p", "480p"],
    duration: 92,
    subtitles: {
      status: "ready",
      url: "/e2e/captions.vtt",
    },
  },
};

export const HISTORY_ITEMS = [
  {
    job_id: JOB_ID,
    status: "done",
    source_filename: AWAITING_JOB.source_filename,
    duration: 92,
    created_at: "2029-12-31T10:00:00Z",
    finished_at: "2029-12-31T10:03:00Z",
    expires_at: "2030-01-02T12:00:00Z",
    poster: "/demo/tideo-test-pattern-poster.webp",
    presets: ["1080p", "720p", "480p"],
    progress: { "1080p": 100, "720p": 100, "480p": 100 },
  },
  {
    job_id: "processing-browser-gate",
    status: "transcoding",
    source_filename: "portrait-interview-source.mov",
    duration: 48,
    created_at: "2029-12-31T11:00:00Z",
    finished_at: null,
    expires_at: null,
    poster: null,
    presets: ["720p", "480p"],
    progress: { "720p": 36, "480p": 62 },
  },
  {
    job_id: "failed-browser-gate",
    status: "failed",
    source_filename: "broken-source.mkv",
    duration: null,
    created_at: "2029-12-31T12:00:00Z",
    finished_at: "2029-12-31T12:00:12Z",
    expires_at: null,
    poster: null,
    presets: null,
    progress: null,
  },
];

export type JobFixtureState =
  | "awaiting_choice"
  | "transcoding"
  | "done"
  | "cancelled"
  | "expired"
  | "notfound"
  | "failed";

export interface ApiFixture {
  state(): JobFixtureState;
  setState(next: JobFixtureState): void;
  sessionHeaders: string[];
}

function jobForState(state: JobFixtureState): object {
  switch (state) {
    case "awaiting_choice":
      return AWAITING_JOB;
    case "transcoding":
      return PROCESSING_JOB;
    case "done":
      return DONE_JOB;
    case "cancelled":
      return { ...PROCESSING_JOB, status: "cancelled" };
    case "expired":
      return { ...PROCESSING_JOB, status: "expired" };
    case "failed":
      return {
        ...PROCESSING_JOB,
        status: "failed",
        error: {
          code: "TRANSCODE_FAILED",
          message: "The encoder stopped before the output could be packaged.",
          stage: "transcode",
          retryable: false,
        },
      };
    case "notfound":
      return {};
  }
}

export async function installApiFixture(
  page: Page,
  initialState: JobFixtureState = "awaiting_choice",
  historyItems: object[] = HISTORY_ITEMS,
): Promise<ApiFixture> {
  let state = initialState;
  const sessionHeaders: string[] = [];

  await page.route("**/readyz", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ready: true }) }),
  );

  await page.route("**/upload?*", (route) => {
    sessionHeaders.push(route.request().headers()["x-tideo-session"] ?? "");
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: JOB_ID, status: "inspecting", dedupe: "miss" }),
    });
  });

  await page.route(`**/jobs/${JOB_ID}/transcode`, (route) => {
    sessionHeaders.push(route.request().headers()["x-tideo-session"] ?? "");
    state = "transcoding";
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ job_id: JOB_ID, status: "queued" }),
    });
  });

  await page.route(`**/jobs/${JOB_ID}/cancel`, (route) => {
    sessionHeaders.push(route.request().headers()["x-tideo-session"] ?? "");
    state = "cancelled";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ job_id: JOB_ID, status: "cancelled" }),
    });
  });

  await page.route(`**/jobs/${JOB_ID}/storyboard`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        url: `/jobs/${JOB_ID}/sprite`,
        tiles: 8,
        cols: 4,
        rows: 2,
        tile_w: 320,
        tile_h: 180,
        interval: 12,
      }),
    }),
  );

  await page.route(`**/jobs/${JOB_ID}/manifest`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: JOB_ID,
        duration: 92,
        web_remuxed: false,
        created_at: "2029-12-31T10:00:00Z",
        storyboard: null,
        renditions: [
          { preset: "1080p", bandwidth: 5_200_000, resolution: "1920x1080", codecs: "avc1.640028,mp4a.40.2" },
          { preset: "720p", bandwidth: 2_800_000, resolution: "1280x720", codecs: "avc1.64001f,mp4a.40.2" },
          { preset: "480p", bandwidth: 1_250_000, resolution: "854x480", codecs: "avc1.4d401e,mp4a.40.2" },
        ],
      }),
    }),
  );

  await page.route(`**/jobs/${JOB_ID}`, (route) => {
    sessionHeaders.push(route.request().headers()["x-tideo-session"] ?? "");
    if (state === "notfound") {
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "NOT_FOUND", message: "not found" } }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(jobForState(state)),
    });
  });

  await page.route("**/jobs?*", (route) => {
    sessionHeaders.push(route.request().headers()["x-tideo-session"] ?? "");
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: historyItems, limit: 24, offset: 0, has_more: false }),
    });
  });

  await page.route("**/e2e/master.m3u8", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/vnd.apple.mpegurl",
      body: [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-STREAM-INF:BANDWIDTH=5200000,RESOLUTION=1920x1080,CODECS=\"avc1.640028,mp4a.40.2\"",
        "/e2e/1080p.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,CODECS=\"avc1.64001f,mp4a.40.2\"",
        "/e2e/720p.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=1250000,RESOLUTION=854x480,CODECS=\"avc1.4d401e,mp4a.40.2\"",
        "/e2e/480p.m3u8",
      ].join("\n"),
    }),
  );

  await page.route("**/e2e/*.m3u8", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/vnd.apple.mpegurl",
      body: "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-ENDLIST\n",
    }),
  );

  await page.route("**/e2e/captions.vtt", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/vtt",
      body: "WEBVTT\n\n00:00.000 --> 00:02.000\nTideo browser fixture\n",
    }),
  );

  return {
    state: () => state,
    setState(next) {
      state = next;
    },
    sessionHeaders,
  };
}

export async function installClosedProgressSocket(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const NativeWebSocket = window.WebSocket;
    class ClosingWebSocket {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSING = 2;
      static readonly CLOSED = 3;
      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSING = 2;
      readonly CLOSED = 3;
      binaryType: BinaryType = "blob";
      bufferedAmount = 0;
      extensions = "";
      protocol = "";
      readyState = ClosingWebSocket.CONNECTING;
      url: string;
      onclose: ((this: WebSocket, ev: CloseEvent) => unknown) | null = null;
      onerror: ((this: WebSocket, ev: Event) => unknown) | null = null;
      onmessage: ((this: WebSocket, ev: MessageEvent) => unknown) | null = null;
      onopen: ((this: WebSocket, ev: Event) => unknown) | null = null;

      constructor(url: string | URL) {
        this.url = String(url);
        if (!this.url.includes("/progress")) return new NativeWebSocket(url) as unknown as ClosingWebSocket;
        window.setTimeout(() => {
          this.readyState = ClosingWebSocket.CLOSED;
          this.onclose?.call(this as unknown as WebSocket, new CloseEvent("close"));
        }, 25);
      }

      addEventListener(): void {}
      removeEventListener(): void {}
      dispatchEvent(): boolean { return true; }
      close(): void { this.readyState = ClosingWebSocket.CLOSED; }
      send(): void {}
    }
    Object.defineProperty(window, "WebSocket", { value: ClosingWebSocket });
  });
}

export async function installClipboardFixture(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "share", { configurable: true, value: undefined });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          (window as Window & { __copiedText?: string }).__copiedText = value;
        },
      },
    });
  });
}

export async function expectNoAxeViolations(
  page: Page,
  testInfo: TestInfo,
  state: string,
): Promise<void> {
  if (testInfo.project.name !== "chromium") return;
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  await testInfo.attach(`axe-${state}`, {
    body: JSON.stringify(results.violations, null, 2),
    contentType: "application/json",
  });
  expect(results.violations, `${state} has axe violations`).toEqual([]);
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content, JSON.stringify(dimensions)).toBeLessThanOrEqual(dimensions.viewport + 1);
}

export async function expectTouchSafeControls(page: Page): Promise<void> {
  const undersized = await page.evaluate(() => {
    const selector = [
      "button",
      "a.btn",
      ".wordmark",
      ".site-nav-link",
      ".site-footer-nav a",
      ".watch-scroll",
      ".hist-card",
      "select",
      "summary",
    ].join(",");
    return Array.from(document.querySelectorAll<HTMLElement>(selector))
      .filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      })
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return { label: element.getAttribute("aria-label") ?? element.textContent?.trim().slice(0, 40), width: rect.width, height: rect.height };
      })
      .filter(({ width, height }) => width < 44 || height < 44);
  });
  expect(undersized, "interactive controls smaller than 44 CSS pixels").toEqual([]);
}

export function watchRuntimeErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  return errors;
}
