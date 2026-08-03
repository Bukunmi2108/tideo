import { expect, test } from "@playwright/test";
import {
  JOB_ID,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectTouchSafeControls,
  installApiFixture,
  installClosedProgressSocket,
  watchRuntimeErrors,
} from "./support";

test("upload validation, inspect, processing reconnect, and cancel remain operable", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const runtimeErrors = watchRuntimeErrors(page);
  await installClosedProgressSocket(page);
  const fixture = await installApiFixture(page, "awaiting_choice");

  await page.goto("/upload");
  await expect(page.getByRole("heading", { level: 1, name: "Upload video" })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: "quarterly-notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("not a video"),
  });
  await expect(page.getByRole("heading", { name: "Unsupported format" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Choose another" })).toBeFocused();
  await expectNoAxeViolations(page, testInfo, "upload-validation-error");
  await page.getByRole("button", { name: "Choose another" }).click();

  await page.locator('input[type="file"]').setInputFiles({
    name: "launch-film.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("synthetic browser fixture"),
  });
  await expect(page).toHaveURL(new RegExp(`/job\\?id=${JOB_ID}$`));
  await expect(page.getByRole("heading", { level: 1, name: /launch-film-final/ })).toBeVisible();
  expect(fixture.sessionHeaders.every(Boolean)).toBe(true);
  await expectNoAxeViolations(page, testInfo, "inspect");
  await expectNoHorizontalOverflow(page);
  await expectTouchSafeControls(page);

  await page.getByRole("checkbox", { name: "Generate captions" }).check();
  await page.getByRole("button", { name: "Start transcoding" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /launch-film-final/ })).toBeVisible();
  await expect(page.getByText("Live updates paused. Checking automatically.")).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "1080p transcoding progress" })).toHaveAttribute("aria-valuenow", "68");
  await expectNoAxeViolations(page, testInfo, "processing-polling");

  await page.getByRole("button", { name: "Cancel job" }).click();
  await expect(page.getByRole("heading", { name: "Cancel transcoding?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel job" })).toBeFocused();
  await expectNoAxeViolations(page, testInfo, "cancel-confirmation");
  await page.getByRole("button", { name: "Cancel job" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Job cancelled" })).toBeVisible();
  await expectNoAxeViolations(page, testInfo, "cancelled");

  expect(fixture.state()).toBe("cancelled");
  expect(runtimeErrors).toEqual([]);
});

test("processing tears down its progress transport after route navigation", async ({ page }) => {
  await page.addInitScript(() => {
    (window as Window & { __progressSocketClosed?: boolean }).__progressSocketClosed = false;
    class TrackedWebSocket {
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
      readyState = TrackedWebSocket.OPEN;
      url: string;
      onclose: ((this: WebSocket, ev: CloseEvent) => unknown) | null = null;
      onerror: ((this: WebSocket, ev: Event) => unknown) | null = null;
      onmessage: ((this: WebSocket, ev: MessageEvent) => unknown) | null = null;
      onopen: ((this: WebSocket, ev: Event) => unknown) | null = null;
      constructor(url: string | URL) {
        this.url = String(url);
        window.setTimeout(() => this.onopen?.call(this as unknown as WebSocket, new Event("open")), 0);
      }
      addEventListener(): void {}
      removeEventListener(): void {}
      dispatchEvent(): boolean { return true; }
      close(): void {
        this.readyState = TrackedWebSocket.CLOSED;
        (window as Window & { __progressSocketClosed?: boolean }).__progressSocketClosed = true;
      }
      send(): void {}
    }
    Object.defineProperty(window, "WebSocket", { value: TrackedWebSocket });
  });
  await installApiFixture(page, "transcoding", []);
  await page.goto(`/job?id=${JOB_ID}`);
  await expect(page.getByText("Transcoding", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "My videos" }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "My videos" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as Window & { __progressSocketClosed?: boolean }).__progressSocketClosed)).toBe(true);
});
