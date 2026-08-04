import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ apiBase: () => "/api" }));
vi.mock("./router", () => ({ navigate: vi.fn() }));
vi.mock("./session", () => ({
  SESSION_HEADER: "X-Tideo-Session",
  guestSession: () => "v1.test-session",
}));
vi.mock("./wake", () => ({ waitForBackendReady: vi.fn() }));

import { navigate } from "./router";
import { mount } from "./upload";
import { waitForBackendReady } from "./wake";

const wakeMock = vi.mocked(waitForBackendReady);
const navigateMock = vi.mocked(navigate);

class MockXhr {
  static instances: MockXhr[] = [];

  upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  status = 0;
  responseText = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn();
  abort = vi.fn(() => this.onabort?.());

  constructor() {
    MockXhr.instances.push(this);
  }
}

function dispatchFiles(kind: "drop" | "paste", files: File[]): void {
  const event = new Event(kind, { bubbles: true, cancelable: true });
  Object.defineProperty(event, kind === "drop" ? "dataTransfer" : "clipboardData", {
    value: { files },
  });
  document.dispatchEvent(event);
}

function video(name = "clip.mp4", size = 8): File {
  return new File([new Uint8Array(size)], name, { type: "video/mp4" });
}

let root: HTMLElement;
let teardown: () => void;
let durationMock: ReturnType<typeof vi.fn<(file: File) => Promise<number | null>>>;

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
  MockXhr.instances = [];
  Object.defineProperty(globalThis, "XMLHttpRequest", {
    configurable: true,
    value: MockXhr,
  });
  Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
  wakeMock.mockResolvedValue(true);
  durationMock = vi.fn().mockResolvedValue(null);
});

afterEach(() => {
  teardown?.();
  root.remove();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("upload mount", () => {
  it("uses one native button for the whole upload surface", () => {
    const inputClick = vi.spyOn(HTMLInputElement.prototype, "click");
    teardown = mount(root, new URLSearchParams(), durationMock);

    const zone = root.querySelector<HTMLButtonElement>("#drop-zone");
    expect(zone?.tagName).toBe("BUTTON");
    zone?.click();
    expect(inputClick).toHaveBeenCalledOnce();
    expect(root.querySelector(".upload-trust")?.textContent).toContain("expire");
    expect(root.querySelector(".upload-hint")?.textContent).toContain("Up to 5 minutes");
  });

  it.each([
    [new File([], "empty.mp4", { type: "video/mp4" }), "The selected file is empty"],
    [video("notes.txt"), "TXT files are not supported"],
  ])("rejects invalid files before waking the backend", (file, message) => {
    teardown = mount(root, new URLSearchParams(), durationMock);
    dispatchFiles("drop", [file]);

    expect(root.textContent).toContain(message);
    expect(wakeMock).not.toHaveBeenCalled();
  });

  it("rejects oversized and multiple-file selections with a direct recovery", () => {
    teardown = mount(root, new URLSearchParams(), durationMock);
    const oversized = video("large.mp4");
    Object.defineProperty(oversized, "size", { value: 4 * 1024 ** 3 + 1 });
    dispatchFiles("drop", [oversized]);
    expect(root.textContent).toContain("The limit is 4 GB");
    expect(root.querySelector("#choose-another-btn")).not.toBeNull();

    dispatchFiles("drop", [video("one.mp4"), video("two.mp4")]);
    expect(root.textContent).toContain("Choose one video at a time");
    expect(wakeMock).not.toHaveBeenCalled();
  });

  it("rejects videos over five minutes before upload", async () => {
    durationMock.mockResolvedValue(301);
    teardown = mount(root, new URLSearchParams(), durationMock);

    dispatchFiles("drop", [video("long.mp4")]);

    await vi.waitFor(() => expect(root.textContent).toContain("The limit is 5 minutes"));
    expect(wakeMock).not.toHaveBeenCalled();
    expect(MockXhr.instances).toHaveLength(0);
  });

  it("accepts a pasted video and begins uploading when the service is ready", async () => {
    teardown = mount(root, new URLSearchParams(), durationMock);
    const file = video("pasted.mp4");
    dispatchFiles("paste", [file]);

    await vi.waitFor(() => expect(MockXhr.instances).toHaveLength(1));
    expect(MockXhr.instances[0].send).toHaveBeenCalledWith(file);
    expect(MockXhr.instances[0].setRequestHeader).toHaveBeenCalledWith(
      "X-Tideo-Session",
      "v1.test-session",
    );
  });

  it("updates semantic progress and announces restrained milestones", async () => {
    teardown = mount(root, new URLSearchParams(), durationMock);
    dispatchFiles("drop", [video("progress.mp4", 100)]);
    await vi.waitFor(() => expect(MockXhr.instances).toHaveLength(1));

    MockXhr.instances[0].upload.onprogress?.({ loaded: 52, total: 100 } as ProgressEvent);

    const progress = root.querySelector("[role=progressbar]");
    expect(progress?.getAttribute("aria-valuenow")).toBe("52");
    expect(progress?.getAttribute("aria-valuetext")).toContain("52 B of 100 B");
    expect(root.querySelector("#upload-live")?.textContent).toContain("52% complete");
  });

  it("cancels an active upload without navigating", async () => {
    teardown = mount(root, new URLSearchParams(), durationMock);
    dispatchFiles("drop", [video()]);
    await vi.waitFor(() => expect(MockXhr.instances).toHaveLength(1));

    root.querySelector<HTMLButtonElement>("#cancel-upload-btn")?.click();

    expect(MockXhr.instances[0].abort).toHaveBeenCalledOnce();
    expect(root.querySelector("#drop-zone")).not.toBeNull();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("cancels upload retry backoff so no later request can restart", async () => {
    vi.useFakeTimers();
    teardown = mount(root, new URLSearchParams(), durationMock);
    dispatchFiles("drop", [video()]);
    await vi.advanceTimersByTimeAsync(0);
    expect(MockXhr.instances).toHaveLength(1);

    MockXhr.instances[0].onerror?.();
    expect(root.textContent).toContain("Retrying upload");
    root.querySelector<HTMLButtonElement>("#cancel-retry-btn")?.click();
    await vi.advanceTimersByTimeAsync(30_000);

    expect(MockXhr.instances).toHaveLength(1);
    expect(root.querySelector("#drop-zone")).not.toBeNull();
  });

  it("keeps a network-failed file available for an explicit retry", async () => {
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    teardown = mount(root, new URLSearchParams(), durationMock);
    dispatchFiles("drop", [video("offline.mp4")]);

    await vi.waitFor(() => expect(root.textContent).toContain("You appear to be offline"));
    expect(root.querySelector("#retry-upload-btn")).not.toBeNull();
    expect(wakeMock).not.toHaveBeenCalled();
  });

  it("maps backend errors to recovery copy instead of exposing raw messages", async () => {
    teardown = mount(root, new URLSearchParams(), durationMock);
    dispatchFiles("drop", [video()]);
    await vi.waitFor(() => expect(MockXhr.instances).toHaveLength(1));
    const xhr = MockXhr.instances[0];
    xhr.status = 503;
    xhr.responseText = JSON.stringify({
      error: {
        code: "STORAGE_PRESSURE",
        message: "internal storage-pressure detail",
        retryable: true,
      },
    });
    xhr.onload?.();

    expect(root.textContent).toContain("Temporary storage is full");
    expect(root.textContent).not.toContain("internal storage-pressure detail");
    expect(root.querySelector("#retry-upload-btn")).not.toBeNull();
  });

  it("rejects malformed success responses instead of navigating to an invalid job", async () => {
    teardown = mount(root, new URLSearchParams(), durationMock);
    dispatchFiles("drop", [video()]);
    await vi.waitFor(() => expect(MockXhr.instances).toHaveLength(1));
    const xhr = MockXhr.instances[0];
    xhr.status = 202;
    xhr.responseText = JSON.stringify({ status: "inspecting", dedupe: "miss" });
    xhr.onload?.();

    expect(root.textContent).toContain("Upload could not finish");
    expect(root.querySelector("#retry-upload-btn")).not.toBeNull();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("presents duplicate uploads as a shortcut to the existing result", async () => {
    teardown = mount(root, new URLSearchParams(), durationMock);
    dispatchFiles("drop", [video("known.mp4")]);
    await vi.waitFor(() => expect(MockXhr.instances).toHaveLength(1));
    const xhr = MockXhr.instances[0];
    xhr.status = 202;
    xhr.responseText = JSON.stringify({ job_id: "known-job", status: "done", dedupe: "hit" });
    xhr.onload?.();

    expect(root.textContent).toContain("known.mp4 was already processed");
    expect(root.querySelector('a[href="/job?id=known-job"]')?.textContent).toBe("View results");
    expect(root.querySelector("#choose-another-btn")?.textContent).toBe("Upload another");
  });
});
