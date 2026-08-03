import { expect, test } from "@playwright/test";
import {
  HISTORY_ITEMS,
  JOB_ID,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectTouchSafeControls,
  installApiFixture,
  watchRuntimeErrors,
} from "./support";

test("My videos covers empty, populated, filtering, and load failure states", async ({ page }, testInfo) => {
  const runtimeErrors = watchRuntimeErrors(page);
  await installApiFixture(page, "awaiting_choice", []);
  await page.goto("/history");
  await expect(page.getByText("No videos yet")).toBeVisible();
  await expectNoAxeViolations(page, testInfo, "history-empty");

  await page.unrouteAll({ behavior: "wait" });
  await installApiFixture(page, "awaiting_choice", HISTORY_ITEMS);
  await page.reload();
  await expect(page.locator(".hist-card")).toHaveCount(3);
  await expect(page.getByRole("progressbar", { name: /portrait-interview-source/ })).toHaveAttribute("aria-valuenow", "49");
  await expectNoAxeViolations(page, testInfo, "history-populated");
  await expectNoHorizontalOverflow(page);
  await expectTouchSafeControls(page);

  await page.getByRole("button", { name: "Ready" }).click();
  await expect(page.getByRole("button", { name: "Ready" })).toHaveAttribute("aria-pressed", "true");
  await expectNoAxeViolations(page, testInfo, "history-filtered");
  expect(runtimeErrors).toEqual([]);
});

test("My videos exposes an actionable full-page error", async ({ page }, testInfo) => {
  await page.route("**/jobs?*", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "SERVICE_UNAVAILABLE", message: "busy", retryable: true } }),
    }),
  );
  await page.goto("/history");
  await expect(page.getByRole("alert")).toContainText("Couldn’t load your videos");
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await expectNoAxeViolations(page, testInfo, "history-error");
});

for (const state of ["expired", "notfound", "failed"] as const) {
  test(`${state} job has an explicit recovery route`, async ({ page }, testInfo) => {
    await installApiFixture(page, state, []);
    await page.goto(`/job?id=${JOB_ID}`);
    const heading = {
      expired: "Outputs expired",
      notfound: "Job not found",
      failed: "Couldn’t finish transcoding",
    }[state];
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByRole("link", { name: /Upload video|My videos/ }).first()).toBeVisible();
    await expectNoAxeViolations(page, testInfo, `job-${state}`);
  });
}
