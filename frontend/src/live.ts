// Transport module — wraps the WebSocket progress endpoint with a polling
// fallback. All screens call watch() and never touch WebSocket directly.

import { apiBase, type JobResponse } from "./api"

// ---- Frame shapes (mirrors app/api/ws.py) ---------------------------------

export interface SnapshotFrame {
  type: "snapshot"
  status: string
  progress: Record<string, number>
}
export interface ProgressFrame {
  type: "progress"
  preset: string
  percent: number
}
export interface StateFrame {
  type: "state"
  status: string
}

export interface WatchHandlers {
  onSnapshot(frame: SnapshotFrame): void
  onProgress(frame: ProgressFrame): void
  onState(frame: StateFrame): void
  onMode(mode: "live" | "polling"): void
}

type WsFrame =
  | SnapshotFrame
  | ProgressFrame
  | StateFrame
  | { type: "ping" }
  | { type: "error"; code: string }

// ---- Constants ------------------------------------------------------------

const TERMINAL = new Set(["done", "failed", "cancelled", "expired"])
const POLL_MS = 2000
const POLL_JITTER_MS = 500
const WS_RETRY_MS = 5000

// ---- URL helpers ----------------------------------------------------------

function wsUrl(jobId: string): string {
  const base = apiBase()
  if (base) {
    const wsBase = base.replace(/^http/, (p) => (p === "http" ? "ws" : "wss"))
    return `${wsBase}/jobs/${jobId}/progress`
  }
  const proto = location.protocol === "https:" ? "wss:" : "ws:"
  return `${proto}//${location.host}/jobs/${jobId}/progress`
}

// ---- watch() --------------------------------------------------------------

export function watch(jobId: string, handlers: WatchHandlers): () => void {
  let dead = false
  let terminal = false
  let mode: "live" | "polling" = "live"
  let ws: WebSocket | null = null
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let wsRetryTimer: ReturnType<typeof setTimeout> | null = null

  function setMode(m: typeof mode): void {
    if (mode !== m) {
      mode = m
      handlers.onMode(m)
    }
  }

  function clear(t: ReturnType<typeof setTimeout> | null): void {
    if (t !== null) clearTimeout(t)
  }

  function teardown(): void {
    dead = true
    ws?.close()
    ws = null
    clear(pollTimer)
    clear(wsRetryTimer)
    pollTimer = null
    wsRetryTimer = null
  }

  function connectWs(): void {
    if (dead) return
    ws = new WebSocket(wsUrl(jobId))

    ws.onopen = () => {
      setMode("live")
      clear(pollTimer)
      pollTimer = null
    }

    ws.onmessage = (e) => {
      if (dead) return
      const frame = JSON.parse(e.data as string) as WsFrame
      if (frame.type === "ping" || frame.type === "error") return
      if (frame.type === "snapshot") handlers.onSnapshot(frame)
      else if (frame.type === "progress") handlers.onProgress(frame)
      else if (frame.type === "state") {
        terminal = true
        handlers.onState(frame)
      }
    }

    ws.onclose = () => {
      ws = null
      if (dead || terminal) return
      startPolling()
      wsRetryTimer = setTimeout(connectWs, WS_RETRY_MS)
    }

    ws.onerror = () => {
      // onclose fires after onerror — handled there
    }
  }

  function startPolling(): void {
    if (dead || terminal) return
    setMode("polling")
    schedulePoll()
  }

  function schedulePoll(): void {
    const delay = POLL_MS + Math.random() * POLL_JITTER_MS
    pollTimer = setTimeout(() => { void poll() }, delay)
  }

  async function poll(): Promise<void> {
    if (dead || terminal) return
    try {
      const resp = await fetch(`${apiBase()}/jobs/${jobId}`)
      if (resp.ok) {
        const job = (await resp.json()) as JobResponse
        handlers.onSnapshot({ type: "snapshot", status: job.status, progress: job.progress ?? {} })
        if (TERMINAL.has(job.status)) {
          terminal = true
          handlers.onState({ type: "state", status: job.status })
          return
        }
      } else if (resp.status === 410) {
        terminal = true
        handlers.onState({ type: "state", status: "expired" })
        return
      }
    } catch {
      // network error — keep polling
    }
    if (!dead && !terminal) schedulePoll()
  }

  connectWs()
  return teardown
}
