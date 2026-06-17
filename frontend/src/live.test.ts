import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { watch, type SnapshotFrame, type ProgressFrame, type StateFrame } from "./live"

// ---- WebSocket mock -------------------------------------------------------

class MockWS {
  static last: MockWS | null = null
  url: string
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  onerror: ((e: unknown) => void) | null = null
  readyState = 0

  constructor(url: string) {
    this.url = url
    MockWS.last = this
  }

  close() {
    this.readyState = 3
    this.onclose?.({ code: 1000 })
  }

  // Helpers used in tests
  open()                  { this.readyState = 1; this.onopen?.() }
  receive(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) }) }
  die()                   { this.onerror?.(new Event("error")); this.readyState = 3; this.onclose?.({ code: 1006 }) }
}

beforeEach(() => {
  MockWS.last = null
  vi.stubGlobal("WebSocket", MockWS)
  vi.useFakeTimers()
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

// ---- Tests ----------------------------------------------------------------

describe("watch() — WebSocket path", () => {
  it("emits snapshot frame on connect", () => {
    const snapshots: SnapshotFrame[] = []
    const teardown = watch("j1", {
      onSnapshot: (f) => snapshots.push(f),
      onProgress: () => {},
      onState:    () => {},
      onMode:     () => {},
    })

    MockWS.last!.open()
    MockWS.last!.receive({ type: "snapshot", status: "transcoding", progress: { "720p": 41.2 } })

    expect(snapshots).toHaveLength(1)
    expect(snapshots[0].status).toBe("transcoding")
    expect(snapshots[0].progress["720p"]).toBe(41.2)
    teardown()
  })

  it("relays progress frames", () => {
    const frames: ProgressFrame[] = []
    const teardown = watch("j1", {
      onSnapshot: () => {},
      onProgress: (f) => frames.push(f),
      onState:    () => {},
      onMode:     () => {},
    })

    MockWS.last!.open()
    MockWS.last!.receive({ type: "progress", preset: "720p", percent: 55.0 })
    MockWS.last!.receive({ type: "progress", preset: "480p", percent: 30.0 })

    expect(frames).toHaveLength(2)
    expect(frames[0]).toEqual({ type: "progress", preset: "720p", percent: 55.0 })
    teardown()
  })

  it("emits state frame on terminal message", () => {
    const states: StateFrame[] = []
    const teardown = watch("j1", {
      onSnapshot: () => {},
      onProgress: () => {},
      onState:    (f) => states.push(f),
      onMode:     () => {},
    })

    MockWS.last!.open()
    MockWS.last!.receive({ type: "state", status: "done" })

    expect(states).toHaveLength(1)
    expect(states[0].status).toBe("done")
    teardown()
  })

  it("ignores ping frames", () => {
    const snapshots: SnapshotFrame[] = []
    const teardown = watch("j1", {
      onSnapshot: (f) => snapshots.push(f),
      onProgress: () => {},
      onState:    () => {},
      onMode:     () => {},
    })

    MockWS.last!.open()
    MockWS.last!.receive({ type: "ping" })

    expect(snapshots).toHaveLength(0)
    teardown()
  })

  it("does not start polling after terminal state close", () => {
    const modes: string[] = []
    const teardown = watch("j1", {
      onSnapshot: () => {},
      onProgress: () => {},
      onState:    () => {},
      onMode:     (m) => modes.push(m),
    })

    const ws = MockWS.last!
    ws.open()
    ws.receive({ type: "state", status: "done" })
    ws.close()  // server closes after terminal

    expect(modes).not.toContain("polling")
    teardown()
  })
})

describe("watch() — fallback to polling", () => {
  it("switches to polling mode when WS dies", () => {
    const modes: string[] = []
    const teardown = watch("j1", {
      onSnapshot: () => {},
      onProgress: () => {},
      onState:    () => {},
      onMode:     (m) => modes.push(m),
    })

    MockWS.last!.die()  // WS error + close without terminal

    expect(modes).toContain("polling")
    teardown()
  })

  it("delivers frames via polling after WS failure", async () => {
    const snapshots: SnapshotFrame[] = []
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      status: 200,
      json: async () => ({ job_id: "j1", status: "transcoding", progress: { "720p": 22.0 } }),
    }))

    const teardown = watch("j1", {
      onSnapshot: (f) => snapshots.push(f),
      onProgress: () => {},
      onState:    () => {},
      onMode:     () => {},
    })

    MockWS.last!.die()
    // Advance past the poll interval (2000-2500ms) but not far enough to
    // trigger the second poll (~4000-5000ms) or WS retry (5000ms).
    await vi.advanceTimersByTimeAsync(3000)
    teardown()

    expect(snapshots.length).toBeGreaterThan(0)
    expect(snapshots[0].status).toBe("transcoding")
  })

  it("stops polling after terminal status in poll response", async () => {
    const states: StateFrame[] = []
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      status: 200,
      json: async () => ({ job_id: "j1", status: "done", progress: {} }),
    }))

    const teardown = watch("j1", {
      onSnapshot: () => {},
      onProgress: () => {},
      onState:    (f) => states.push(f),
      onMode:     () => {},
    })

    MockWS.last!.die()
    await vi.runAllTimersAsync()

    expect(states).toHaveLength(1)
    expect(states[0].status).toBe("done")
    teardown()
  })
})

describe("watch() — teardown", () => {
  it("closes the WebSocket on teardown", () => {
    const teardown = watch("j1", { onSnapshot: () => {}, onProgress: () => {}, onState: () => {}, onMode: () => {} })
    const ws = MockWS.last!
    ws.open()
    teardown()
    expect(ws.readyState).toBe(3)
  })

  it("does not deliver frames after teardown", () => {
    const snapshots: SnapshotFrame[] = []
    const teardown = watch("j1", {
      onSnapshot: (f) => snapshots.push(f),
      onProgress: () => {},
      onState:    () => {},
      onMode:     () => {},
    })

    const ws = MockWS.last!
    ws.open()
    teardown()
    ws.receive({ type: "snapshot", status: "transcoding", progress: {} })

    expect(snapshots).toHaveLength(0)
  })
})
