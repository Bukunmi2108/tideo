import { expect, test } from "@playwright/test";
import { watchRuntimeErrors } from "./support";

test("homepage demo plays adaptive Sintel renditions with preprocessed captions", async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== "chromium", "The real checked-in HLS package is exercised once in Chromium.");
  const runtimeErrors = watchRuntimeErrors(page);
  const mediaRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith(".m4s")) mediaRequests.push(request.url());
  });

  await page.goto("/");

  const quality = page.getByRole("combobox", { name: "Playback quality" });
  await expect.poll(async () => quality.locator("option").count()).toBe(4);
  await expect(page.locator(".pl-cc")).toBeVisible();

  await page.getByRole("link", { name: "Watch demo" }).click();
  const video = page.locator("#demo-player video");
  await expect.poll(() => video.evaluate((item) => (item as HTMLVideoElement).currentTime)).toBeGreaterThan(0);
  await expect.poll(() => mediaRequests.length).toBeGreaterThan(0);

  const lowRequestsBefore = mediaRequests.filter((url) => url.includes("/240p/")).length;
  await page.locator('[data-demo-quality="240"]').click();
  await expect(quality.locator("option:checked")).toHaveText("240p");
  await expect(page.locator('[data-demo-quality="240"]')).toHaveAttribute("aria-pressed", "true");
  await expect.poll(
    () => mediaRequests.filter((url) => url.includes("/240p/")).length,
    { timeout: 5_000 },
  ).toBeGreaterThan(lowRequestsBefore);

  const captions = page.locator(".pl-cc");
  await page.locator("#demo-player").hover();
  await captions.click();
  await expect(captions).toHaveAttribute("aria-pressed", "true");
  await video.evaluate((item) => {
    const media = item as HTMLVideoElement;
    media.pause();
    media.currentTime = 12.5;
  });
  await expect.poll(() => video.evaluate((item) => {
    const track = (item as HTMLVideoElement).textTracks[0];
    return Array.from(track?.activeCues ?? [])
      .map((cue) => (cue as VTTCue).text)
      .join(" ");
  })).toContain("land of the gatekeepers");

  await video.evaluate((item) => {
    (item as HTMLVideoElement).currentTime = 42.5;
  });
  await expect.poll(() => video.evaluate((item) => {
    const track = (item as HTMLVideoElement).textTracks[0];
    return Array.from(track?.activeCues ?? [])
      .map((cue) => (cue as VTTCue).text)
      .join(" ");
  })).toContain("as long as I can remember");

  expect(runtimeErrors).toEqual([]);
});
