import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import type { JobListResponse, JobSummary } from "./api"

vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  listJobs: vi.fn(),
}))

import { listJobs } from "./api"
import { mount } from "./history"

const listMock = listJobs as unknown as ReturnType<typeof vi.fn>

function job(over: Partial<JobSummary> = {}): JobSummary {
  return {
    job_id: "j1", status: "done", source_filename: "clip.mp4", duration: 60,
    created_at: "2026-06-17T11:00:00Z", finished_at: "2026-06-17T11:01:00Z",
    expires_at: "2026-06-24T11:01:00Z", poster: "/jobs/j1/poster", ...over,
  }
}

function page(items: JobSummary[], has_more = false): JobListResponse {
  return { items, limit: 24, offset: 0, has_more }
}

let root: HTMLElement
let teardown: () => void

beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root) })
afterEach(() => { teardown?.(); root.remove(); vi.clearAllMocks() })

describe("history mount", () => {
  it("renders a card per job with filename, badge, and a poster image", async () => {
    listMock.mockResolvedValue(page([job(), job({ job_id: "j2", source_filename: "two.mov" })]))
    teardown = mount(root)
    await vi.waitFor(() => expect(root.querySelectorAll(".hist-card").length).toBe(2))
    expect(root.querySelector(".hist-name")?.textContent).toBe("clip.mp4")
    expect(root.querySelector(".hist-badge--done")?.textContent).toBe("done")
    expect(root.querySelector("img.hist-poster")?.getAttribute("src")).toMatch(/\/jobs\/j1\/poster$/)
    expect(root.querySelector(".hist-card")?.getAttribute("href")).toBe("/job?id=j1")
  })

  it("uses the placeholder (no img) when a poster is gone", async () => {
    listMock.mockResolvedValue(page([job({ status: "expired", poster: null })]))
    teardown = mount(root)
    await vi.waitFor(() => expect(root.querySelector(".hist-badge--expired")).toBeTruthy())
    expect(root.querySelector("img.hist-poster")).toBeNull()
    expect(root.querySelector(".hist-poster--empty")).toBeTruthy()
  })

  it("shows the empty state with a CTA when there are no jobs", async () => {
    listMock.mockResolvedValue(page([]))
    teardown = mount(root)
    await vi.waitFor(() => expect(root.querySelector(".hist-empty")).toBeTruthy())
    expect(root.querySelector(".hist-empty .btn")?.getAttribute("href")).toBe("/")
    expect(root.querySelector(".hist-grid")).toBeNull()
  })

  it("paginates: a Load more button fetches the next page and appends", async () => {
    listMock.mockResolvedValueOnce(page([job({ job_id: "a" })], true))
    teardown = mount(root)
    await vi.waitFor(() => expect(root.querySelector(".hist-more")).toBeTruthy())
    listMock.mockResolvedValueOnce(page([job({ job_id: "b" })], false))
    ;(root.querySelector(".hist-more") as HTMLButtonElement).click()
    await vi.waitFor(() => expect(root.querySelectorAll(".hist-card").length).toBe(2))
    expect(root.querySelector(".hist-more")).toBeNull()           // gone once the last page loads
    expect(listMock).toHaveBeenLastCalledWith({ limit: 24, offset: 1 }, expect.anything())
  })

  it("does not throw if torn down before the fetch resolves", async () => {
    let resolve!: (v: JobListResponse) => void
    listMock.mockReturnValue(new Promise((r) => { resolve = r }))
    teardown = mount(root)
    teardown()                                                    // unmount mid-flight
    resolve(page([job()]))
    await Promise.resolve()
    expect(root.querySelector(".hist-name")).toBeNull()           // late result ignored (only the skeleton remains)
  })
})
