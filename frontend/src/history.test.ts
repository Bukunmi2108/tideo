import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { JobListResponse, JobSummary } from "./api";

vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  listJobs: vi.fn(),
}));

import { listJobs } from "./api";
import { mount } from "./history";

const listMock = listJobs as unknown as ReturnType<typeof vi.fn>;

function job(over: Partial<JobSummary> = {}): JobSummary {
  return {
    job_id: "j1",
    status: "done",
    source_filename: "clip.mp4",
    duration: 60,
    created_at: "2026-06-17T11:00:00Z",
    finished_at: "2026-06-17T11:01:00Z",
    expires_at: "2026-06-24T11:01:00Z",
    poster: "/jobs/j1/poster",
    ...over,
  };
}

function page(items: JobSummary[], has_more = false): JobListResponse {
  return { items, limit: 24, offset: 0, has_more };
}

let root: HTMLElement;
let teardown: () => void;

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
});
afterEach(() => {
  teardown?.();
  root.remove();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("history mount", () => {
  it("shows a useful session header, filters, and an accessible loading state", () => {
    listMock.mockReturnValue(new Promise(() => {}));
    teardown = mount(root);

    expect(root.querySelector("h1")?.textContent).toBe("My videos");
    expect(root.textContent).toContain("this browser session");
    expect(root.querySelector('.hist-toolbar a[href="/upload"]')).not.toBeNull();
    expect(root.querySelectorAll(".hist-filter")).toHaveLength(4);
    expect(root.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(root.querySelector(".hero")).toBeNull();
  });

  it("renders a card per job with filename, badge, and a poster image", async () => {
    listMock.mockResolvedValue(
      page([job(), job({ job_id: "j2", source_filename: "two.mov" })]),
    );
    teardown = mount(root);
    expect(root.querySelector("h1")?.textContent).toBe("My videos");
    await vi.waitFor(() =>
      expect(root.querySelectorAll(".hist-card").length).toBe(2),
    );
    expect(root.querySelector(".hist-name")?.textContent).toBe("clip.mp4");
    expect(root.querySelector(".hist-badge--done")?.textContent).toBe("Ready");
    expect(root.querySelector("img.hist-poster")?.getAttribute("src")).toMatch(
      /\/jobs\/j1\/poster$/,
    );
    expect(root.querySelector(".hist-card")?.getAttribute("href")).toBe(
      "/job?id=j1",
    );
  });

  it("uses the placeholder (no img) when a poster is gone", async () => {
    listMock.mockResolvedValue(
      page([job({ status: "expired", poster: null })]),
    );
    teardown = mount(root);
    await vi.waitFor(() =>
      expect(root.querySelector(".hist-badge--expired")).toBeTruthy(),
    );
    expect(root.querySelector("img.hist-poster")).toBeNull();
    expect(root.querySelector(".hist-poster--empty")).toBeTruthy();
  });

  it("shows the empty state with a CTA when there are no jobs", async () => {
    listMock.mockResolvedValue(page([]));
    teardown = mount(root);
    await vi.waitFor(() =>
      expect(root.querySelector(".hist-empty")).toBeTruthy(),
    );
    expect(root.querySelector(".hist-empty .btn")?.getAttribute("href")).toBe(
      "/upload",
    );
    expect(root.querySelector(".hist-grid")).toBeNull();
  });

  it("paginates: a Load more button fetches the next page and appends", async () => {
    listMock.mockResolvedValueOnce(page([job({ job_id: "a" })], true));
    teardown = mount(root);
    await vi.waitFor(() =>
      expect(root.querySelector(".hist-more")).toBeTruthy(),
    );
    listMock.mockResolvedValueOnce(page([job({ job_id: "b" })], false));
    (root.querySelector(".hist-more") as HTMLButtonElement).click();
    await vi.waitFor(() =>
      expect(root.querySelectorAll(".hist-card").length).toBe(2),
    );
    expect(root.querySelector(".hist-more")).toBeNull(); // gone once the last page loads
    expect(listMock).toHaveBeenLastCalledWith(
      { limit: 24, offset: 1 },
      expect.anything(),
    );
  });

  it("filters processing jobs through the grouped API filter", async () => {
    listMock.mockResolvedValueOnce(page([job()]));
    teardown = mount(root);
    await vi.waitFor(() => expect(root.querySelector(".hist-grid")).toBeTruthy());
    listMock.mockResolvedValueOnce(
      page([job({ status: "transcoding", progress: { "720p": 45 } })]),
    );

    (root.querySelector('[data-filter="processing"]') as HTMLButtonElement).click();

    await vi.waitFor(() =>
      expect(listMock).toHaveBeenLastCalledWith(
        { limit: 24, offset: 0, status: "processing" },
        expect.anything(),
      ),
    );
    expect(root.querySelector('[data-filter="processing"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(root.querySelector(".hist-progress")?.textContent).toContain("45%");
    expect(root.querySelector("[role=progressbar]")?.getAttribute("aria-valuenow")).toBe("45");
  });

  it("keeps existing cards visible when pagination fails and retries in place", async () => {
    listMock.mockResolvedValueOnce(page([job({ job_id: "a" })], true));
    teardown = mount(root);
    await vi.waitFor(() => expect(root.querySelector(".hist-more")).toBeTruthy());
    listMock.mockRejectedValueOnce(new Error("offline"));

    (root.querySelector(".hist-more") as HTMLButtonElement).click();

    await vi.waitFor(() => expect(root.querySelector(".hist-page-error")).toBeTruthy());
    expect(root.querySelectorAll(".hist-card")).toHaveLength(1);
    expect(root.querySelector(".hist-name")?.textContent).toBe("clip.mp4");
    listMock.mockResolvedValueOnce(page([job({ job_id: "b" })]));
    (root.querySelector(".hist-page-retry") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(root.querySelectorAll(".hist-card")).toHaveLength(2));
  });

  it("offers a retry after a full-page loading error", async () => {
    listMock.mockRejectedValueOnce(new Error("offline"));
    teardown = mount(root);
    await vi.waitFor(() => expect(root.querySelector(".hist-load-error")).toBeTruthy());
    expect(root.querySelector(".hist-retry")).not.toBeNull();

    listMock.mockResolvedValueOnce(page([job()]));
    (root.querySelector(".hist-retry") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(root.querySelector(".hist-card")).toBeTruthy());
  });

  it("refreshes visible processing jobs and stops once they are ready", async () => {
    vi.useFakeTimers();
    listMock.mockResolvedValueOnce(
      page([job({ status: "transcoding", progress: { "720p": 20 } })]),
    );
    teardown = mount(root);
    await vi.advanceTimersByTimeAsync(0);
    expect(root.querySelector(".hist-progress")?.textContent).toContain("20%");

    listMock.mockResolvedValueOnce(page([job({ status: "done" })]));
    await vi.advanceTimersByTimeAsync(5000);

    expect(root.querySelector(".hist-badge--done")?.textContent).toBe("Ready");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not throw if torn down before the fetch resolves", async () => {
    let resolve!: (v: JobListResponse) => void;
    listMock.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    teardown = mount(root);
    teardown(); // unmount mid-flight
    resolve(page([job()]));
    await Promise.resolve();
    expect(root.querySelector(".hist-name")).toBeNull(); // late result ignored (only the skeleton remains)
  });
});
