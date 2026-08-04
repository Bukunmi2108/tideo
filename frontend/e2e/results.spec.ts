import { expect, test } from "@playwright/test";
import {
  JOB_ID,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectTouchSafeControls,
  installApiFixture,
  installClipboardFixture,
  installPlayerMediaFixture,
  watchRuntimeErrors,
} from "./support";

test("completed playback exposes quality, captions, share, download, and teardown", async ({ page, browserName }, testInfo) => {
  const runtimeErrors = watchRuntimeErrors(page);
  const scripts: string[] = [];
  let playlistRequests = 0;
  page.on("response", (response) => {
    if (response.request().resourceType() === "script") scripts.push(response.url());
  });
  page.on("request", (request) => {
    if (request.url().includes("/e2e/") && request.url().endsWith(".m3u8")) playlistRequests += 1;
  });
  await installClipboardFixture(page);
  await installApiFixture(page, "done", []);

  await page.goto(`/job?id=${JOB_ID}`);
  await expect(page.getByRole("heading", { level: 1, name: /launch-film-final/ })).toBeVisible();
  const quality = page.getByRole("combobox", { name: "Playback quality" });
  if (browserName === "webkit" && !(await quality.isVisible())) {
    await expect(quality).toBeHidden();
    await expect(page.locator("video")).toHaveAttribute("src", /master\.m3u8/);
  } else {
    await expect(quality).toBeVisible();
    await expect.poll(async () => quality.locator("option").count()).toBeGreaterThan(1);
    await quality.selectOption("0");
    await expect(quality).toHaveValue("0");
  }
  if (browserName !== "webkit")
    await expect(page.getByRole("button", { name: /captions/i })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download MP4" })).toHaveAttribute("download", "");
  await expectNoAxeViolations(page, testInfo, "complete-player-quality");
  await expectNoHorizontalOverflow(page);
  await expectTouchSafeControls(page);

  await page.getByRole("button", { name: "Share video" }).click();
  await expect(page.getByRole("status")).toHaveText("Share link copied.");
  await expect.poll(() => page.evaluate(() => (window as Window & { __copiedText?: string }).__copiedText)).toContain(`/job?id=${JOB_ID}`);
  await expectNoAxeViolations(page, testInfo, "complete-share-feedback");

  expect(scripts.some((url) => /(?:player|hls)/i.test(url)), scripts.join("\n")).toBe(true);
  const requestsBeforeNavigation = playlistRequests;
  await page.getByRole("link", { name: "My videos" }).first().click();
  await expect(page.getByText("No videos yet")).toBeVisible();
  await page.waitForTimeout(300);
  expect(playlistRequests).toBe(requestsBeforeNavigation);
  expect(runtimeErrors).toEqual([]);
});

test("automatic playback waits for Play and captions activate midstream", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "Real HLS media behavior is covered once in Chromium.");
  const runtimeErrors = watchRuntimeErrors(page);
  const segmentRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith(".ts")) segmentRequests.push(request.url());
  });
  await installClipboardFixture(page);
  await installApiFixture(page, "done", []);
  await installPlayerMediaFixture(page);

  await page.goto(`/job?id=${JOB_ID}`);
  const quality = page.getByRole("combobox", { name: "Playback quality" });
  await expect.poll(async () => quality.locator("option").count()).toBe(3);
  expect(segmentRequests).toEqual([]);

  await page.getByRole("button", { name: "Play video" }).click();
  await expect.poll(() => segmentRequests.length).toBeGreaterThan(0);
  expect(segmentRequests[0]).toContain("/240p/");
  await expect(quality.locator("option").first()).toContainText(/Auto · (240|480)p/);

  const video = page.locator("video");
  await expect.poll(() => video.evaluate((element) => (element as HTMLVideoElement).duration)).toBeGreaterThan(7);
  await video.evaluate((element) => {
    const media = element as HTMLVideoElement;
    media.pause();
    media.currentTime = 2.5;
  });
  const captions = page.locator(".pl-cc");
  await page.locator(".player").hover();
  await expect(captions).toBeVisible();
  await expect(captions).toHaveAttribute("aria-label", "Turn captions on");
  await captions.click();
  await expect(captions).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("status").filter({ hasText: "Captions on" })).toBeVisible();
  await expect.poll(() => video.evaluate((element) => {
    const track = (element as HTMLVideoElement).textTracks[0];
    return Array.from(track?.activeCues ?? [])
      .map((cue) => (cue as VTTCue).text)
      .join(" ");
  })).toContain("Captions work during playback");

  await video.evaluate((element) => {
    (element as HTMLVideoElement).currentTime = 5;
  });
  await expect.poll(() => video.evaluate((element) =>
    (element as HTMLVideoElement).textTracks[0]?.activeCues?.length ?? 0,
  )).toBe(0);
  await expect(captions).toHaveAttribute("aria-pressed", "true");
  expect(runtimeErrors).toEqual([]);
});

test("completed player remains usable at 360px with portrait-safe containment", async ({ page, browserName }, testInfo) => {
  test.skip(browserName !== "chromium", "The narrow viewport is covered once; the completed journey runs in every engine.");
  await page.setViewportSize({ width: 360, height: 800 });
  await installClipboardFixture(page);
  await installApiFixture(page, "done", []);
  await page.goto(`/job?id=${JOB_ID}`);
  await expect(page.getByRole("combobox", { name: "Playback quality" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectTouchSafeControls(page);
  await expectNoAxeViolations(page, testInfo, "complete-mobile");
  const videoBox = await page.locator(".player-video").boundingBox();
  expect(videoBox?.width ?? 0).toBeLessThanOrEqual(360);
  expect(videoBox?.height ?? 0).toBeGreaterThan(0);
});
