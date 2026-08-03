import { expect, test } from "@playwright/test";
import {
  JOB_ID,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectTouchSafeControls,
  installApiFixture,
  installClipboardFixture,
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
    await expect(page.getByRole("button", { name: "Captions" })).toBeVisible();
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
