import { expect, test } from "@playwright/test";
import {
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectTouchSafeControls,
  installApiFixture,
  watchRuntimeErrors,
} from "./support";

test("overview routes into the upload journey with keyboard and route focus", async ({ page }, testInfo) => {
  const runtimeErrors = watchRuntimeErrors(page);
  const loadedScripts: string[] = [];
  page.on("response", (response) => {
    if (response.request().resourceType() === "script") loadedScripts.push(response.url());
  });

  await installApiFixture(page, "awaiting_choice", []);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "One upload. Every playback size." })).toBeVisible();
  await expectNoAxeViolations(page, testInfo, "overview");
  await expectNoHorizontalOverflow(page);
  await expectTouchSafeControls(page);

  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to content" });
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toBeVisible();
  await skipLink.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  await page.getByRole("link", { name: "Upload video" }).first().focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/upload$/);
  await expect(page.getByRole("heading", { level: 1, name: "Upload video" })).toBeFocused();
  await expectNoAxeViolations(page, testInfo, "upload-idle");

  expect(loadedScripts.some((url) => /(?:player|hls\.js)/i.test(url)), loadedScripts.join("\n")).toBe(false);
  expect(runtimeErrors).toEqual([]);
});

test("guest identity persists in one browser context and differs in another", async ({ browser }) => {
  async function readSessionHeader(): Promise<string> {
    const context = await browser.newContext();
    const page = await context.newPage();
    const fixture = await installApiFixture(page, "awaiting_choice", []);
    await page.goto("/history");
    await expect(page.getByText("No videos yet")).toBeVisible();
    const first = fixture.sessionHeaders.at(-1) ?? "";
    await page.reload();
    await expect(page.getByText("No videos yet")).toBeVisible();
    expect(fixture.sessionHeaders.at(-1)).toBe(first);
    await context.close();
    return first;
  }

  const first = await readSessionHeader();
  const second = await readSessionHeader();
  expect(first).toMatch(/^v1\.[A-Za-z0-9_-]{43}$/);
  expect(second).toMatch(/^v1\.[A-Za-z0-9_-]{43}$/);
  expect(second).not.toBe(first);
});

test.describe("responsive overview", () => {
  const viewports = [
    { name: "mobile-360", width: 360, height: 800 },
    { name: "mobile-390", width: 390, height: 844 },
    { name: "tablet-portrait", width: 768, height: 1024 },
    { name: "tablet-landscape", width: 1024, height: 768 },
    { name: "desktop", width: 1440, height: 900 },
    { name: "wide-desktop", width: 1920, height: 1080 },
  ];

  for (const viewport of viewports) {
    test(`${viewport.name} keeps the primary action and layout intact`, async ({ page, browserName }) => {
      test.skip(browserName !== "chromium", "The full viewport matrix runs once; engine coverage runs in the journey tests.");
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/");
      const primaryAction = page.getByRole("link", { name: "Upload video" }).first();
      await expect(primaryAction).toBeVisible();
      const box = await primaryAction.boundingBox();
      expect(box?.y ?? Infinity).toBeLessThan(viewport.height);
      await expectNoHorizontalOverflow(page);
    });
  }
});
