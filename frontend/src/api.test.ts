import { describe, it, expect, vi, afterEach } from "vitest"
import { getJob, postTranscode, ApiError } from "./api"

function mockFetch(body: unknown, status = 200) {
  vi.stubGlobal("fetch", async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }))
}

afterEach(() => vi.restoreAllMocks())

describe("getJob", () => {
  it("parses a transcoding response", async () => {
    mockFetch({ job_id: "j1", status: "transcoding", progress: { "720p": 41.2, "480p": 12.0 } })
    const job = await getJob("j1")
    expect(job.status).toBe("transcoding")
    expect(job.progress?.["720p"]).toBe(41.2)
    expect(job.progress?.["480p"]).toBe(12.0)
  })

  it("parses an awaiting_choice response", async () => {
    mockFetch({
      job_id: "j1",
      status: "awaiting_choice",
      source: { duration: 30, width: 1280, height: 720, video_codec: "h264", has_audio: true, audio_codec: "aac", container: "mp4", bitrate: 5000000, fps: 30, video_streams: 1, audio_streams: 1 },
      recommended_presets: ["720p", "480p"],
      web_safe: true,
      web_safe_reason: null,
    })
    const job = await getJob("j1")
    expect(job.status).toBe("awaiting_choice")
    expect(job.recommended_presets).toEqual(["720p", "480p"])
    expect(job.web_safe).toBe(true)
    expect(job.source?.width).toBe(1280)
  })

  it("parses a done response", async () => {
    mockFetch({
      job_id: "j1",
      status: "done",
      results: {
        playlist: "/jobs/j1/playlist", web_mp4: "/jobs/j1/file", poster: "/jobs/j1/poster",
        sprite: "/jobs/j1/sprite", player: "/jobs/j1/player", presets: ["720p", "480p"], duration: 60,
      },
    })
    const job = await getJob("j1")
    expect(job.status).toBe("done")
    expect(job.results?.playlist).toBe("/jobs/j1/playlist")
    expect(job.results?.presets).toEqual(["720p", "480p"])
  })

  it("throws ApiError on 404", async () => {
    mockFetch({ error: { code: "JOB_NOT_FOUND", message: "no such job", job_id: "j1", retryable: false } }, 404)
    await expect(getJob("j1")).rejects.toMatchObject({ code: "JOB_NOT_FOUND" })
    await expect(getJob("j1")).rejects.toBeInstanceOf(ApiError)
  })

  it("throws ApiError on 410", async () => {
    mockFetch({ error: { code: "JOB_EXPIRED", message: "expired", job_id: "j1", retryable: false } }, 410)
    const err = await getJob("j1").catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(410)
  })

  it("ApiError includes retryable flag", async () => {
    mockFetch({ error: { code: "SERVER_ERROR", message: "oops", job_id: null, retryable: true } }, 500)
    const err = await getJob("j1").catch((e: unknown) => e)
    expect((err as ApiError).retryable).toBe(true)
  })
})

describe("postTranscode", () => {
  it("sends correct body and returns result", async () => {
    let captured: string | null = null
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      captured = init?.body as string
      return { ok: true, status: 202, json: async () => ({ job_id: "j1", status: "queued" }) }
    })
    const result = await postTranscode("j1", { presets: ["720p", "480p"], subtitles: false })
    expect(result.status).toBe("queued")
    const body = JSON.parse(captured!)
    expect(body.presets).toEqual(["720p", "480p"])
    expect(body.subtitles).toBe(false)
  })

  it("throws ApiError on 409 wrong state", async () => {
    mockFetch({ error: { code: "WRONG_STATE", message: "not awaiting_choice", job_id: "j1", retryable: false } }, 409)
    await expect(postTranscode("j1", { presets: ["720p"], subtitles: false })).rejects.toMatchObject({ code: "WRONG_STATE" })
  })
})
