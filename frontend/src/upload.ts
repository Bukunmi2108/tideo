import { apiBase, type UploadResponse } from "./api"
import { navigate } from "./router"
import { esc, humanBytes } from "./render"

// ---- State ----------------------------------------------------------------

type State =
  | { tag: "idle" }
  | { tag: "uploading"; file: File; loaded: number; total: number; rate: number }
  | { tag: "waking"; file: File; attempt: number }
  | { tag: "dedup"; jobId: string; filename: string }
  | { tag: "rejected"; code: string; message: string }

const ALLOWED_EXTS = [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"]
const MAX_BYTES = 4 * 1024 ** 3
const MAX_WAKING_ATTEMPTS = 6

// ---- Pure helpers ---------------------------------------------------------

const ERROR_HEADLINES: Record<string, string> = {
  UPLOAD_TOO_LARGE:   "File too large",
  UNSUPPORTED_MEDIA:  "Unsupported format",
  INVALID_UPLOAD:     "Invalid file",
  NETWORK_ERROR:      "Connection failed",
  SERVER_ERROR:       "Server error",
}
function errorHeadline(code: string): string {
  return ERROR_HEADLINES[code] ?? "Upload failed"
}

function iconUpload(): string {
  return `<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" width="48" height="48">
    <path d="M24 32V16M16 24l8-8 8 8"/>
    <path d="M8 36a8 8 0 0 1 0-16h2a12 12 0 1 1 24 0h2a8 8 0 0 1 0 16"/>
  </svg>`
}
function iconCheck(): string {
  return `<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" width="48" height="48">
    <circle cx="24" cy="24" r="20"/><path d="M15 24l6 6 12-12"/>
  </svg>`
}
function iconX(): string {
  return `<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" width="48" height="48">
    <circle cx="24" cy="24" r="20"/><path d="M30 18L18 30M18 18l12 12"/>
  </svg>`
}
function iconSpinner(): string {
  return `<svg class="spin" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" width="48" height="48">
    <circle cx="24" cy="24" r="18" stroke-opacity="0.2"/><path d="M24 6a18 18 0 0 1 18 18"/>
  </svg>`
}

// ---- Mount ----------------------------------------------------------------

export function mount(root: HTMLElement): () => void {
  let state: State = { tag: "idle" }
  let dragCount = 0
  let currentXhr: XMLHttpRequest | null = null
  const ac = new AbortController()
  const { signal } = ac

  // hidden file input lives for this mount only
  const fileInput = document.createElement("input")
  fileInput.type = "file"
  fileInput.accept = ALLOWED_EXTS.join(",")
  fileInput.style.display = "none"
  document.body.appendChild(fileInput)

  function setState(next: State): void {
    state = next
    render()
  }

  function render(): void {
    root.innerHTML = `<main class="upload-main">${card()}</main>`
    bind()
  }

  function card(): string {
    switch (state.tag) {
      case "idle": return cardIdle()
      case "uploading": return cardUploading(state)
      case "waking": return cardWaking()
      case "dedup": return cardDedup(state)
      case "rejected": return cardRejected(state)
    }
  }

  function cardIdle(): string {
    return `
      <div class="upload-card upload-zone" id="drop-zone" tabindex="0" role="button" aria-label="Upload a video file">
        <div class="upload-icon">${iconUpload()}</div>
        <p class="upload-headline">Drop a video here</p>
        <p class="upload-sub">or <span class="link-text" id="browse-trigger">click to browse</span></p>
        <p class="upload-hint">${ALLOWED_EXTS.map(e => e.slice(1).toUpperCase()).join(" · ")} · up to 4 GB</p>
      </div>`
  }

  function cardUploading(s: Extract<State, { tag: "uploading" }>): string {
    const pct = s.total > 0 ? Math.round((s.loaded / s.total) * 100) : 0
    const rateStr = s.rate > 0 ? ` · ${humanBytes(s.rate)}/s` : ""
    return `
      <div class="upload-card">
        <p class="upload-filename" title="${esc(s.file.name)}">${esc(s.file.name)}</p>
        <div class="progress-bar-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Upload progress">
          <div class="progress-bar-fill" style="width: ${pct}%"></div>
        </div>
        <p class="upload-stats">${humanBytes(s.loaded)} / ${humanBytes(s.total)}${rateStr}</p>
        <button class="btn btn-ghost" id="cancel-btn" type="button">Cancel</button>
      </div>`
  }

  function cardWaking(): string {
    return `
      <div class="upload-card">
        <div class="upload-icon">${iconSpinner()}</div>
        <p class="upload-headline">Waking up…</p>
        <p class="upload-sub">The demo is starting, please wait.</p>
      </div>`
  }

  function cardDedup(s: Extract<State, { tag: "dedup" }>): string {
    return `
      <div class="upload-card">
        <div class="upload-icon upload-icon--success">${iconCheck()}</div>
        <p class="upload-headline">Already transcoded</p>
        <p class="upload-sub">This exact file was processed before.</p>
        <a href="/job?id=${esc(s.jobId)}" class="btn btn-primary">View results →</a>
        <button class="btn btn-ghost" id="restart-btn" type="button">Upload a different file</button>
      </div>`
  }

  function cardRejected(s: Extract<State, { tag: "rejected" }>): string {
    return `
      <div class="upload-card">
        <div class="upload-icon upload-icon--danger">${iconX()}</div>
        <p class="upload-headline">${errorHeadline(s.code)}</p>
        <p class="upload-sub">${esc(s.message)}</p>
        <button class="btn btn-primary" id="restart-btn" type="button">Try again</button>
      </div>`
  }

  function bind(): void {
    root.querySelector("#browse-trigger")?.addEventListener("click", () => fileInput.click())
    root.querySelector("#drop-zone")?.addEventListener("keydown", (e) => {
      const ke = e as KeyboardEvent
      if (ke.key === "Enter" || ke.key === " ") { ke.preventDefault(); fileInput.click() }
    })
    root.querySelector("#cancel-btn")?.addEventListener("click", () => {
      currentXhr?.abort()
      currentXhr = null
      setState({ tag: "idle" })
    })
    root.querySelector("#restart-btn")?.addEventListener("click", () => setState({ tag: "idle" }))
  }

  function handleFile(file: File): void {
    const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase()
    if (!ALLOWED_EXTS.includes(ext)) {
      setState({ tag: "rejected", code: "UNSUPPORTED_MEDIA", message: `${ext} files are not supported. Please upload a video file.` })
      return
    }
    if (file.size > MAX_BYTES) {
      setState({ tag: "rejected", code: "UPLOAD_TOO_LARGE", message: `File is ${humanBytes(file.size)} — the limit is 4 GB.` })
      return
    }
    startUpload(file, 0)
  }

  function startUpload(file: File, attempt: number): void {
    currentXhr?.abort()
    if (attempt > 0) setState({ tag: "waking", file, attempt })
    else setState({ tag: "uploading", file, loaded: 0, total: file.size, rate: 0 })

    let lastBytes = 0
    let lastTime = Date.now()
    const xhr = new XMLHttpRequest()
    currentXhr = xhr
    xhr.open("POST", `${apiBase()}/upload?filename=${encodeURIComponent(file.name)}`)
    xhr.setRequestHeader("Content-Type", "application/octet-stream")

    xhr.upload.onprogress = (e) => {
      const now = Date.now()
      const elapsed = (now - lastTime) / 1000
      const rate = elapsed > 0 ? (e.loaded - lastBytes) / elapsed : 0
      lastBytes = e.loaded
      lastTime = now
      if (state.tag === "uploading") setState({ tag: "uploading", file, loaded: e.loaded, total: e.total, rate: Math.max(0, rate) })
    }

    xhr.onload = () => {
      currentXhr = null
      if (xhr.status === 202) {
        const resp = JSON.parse(xhr.responseText) as UploadResponse
        if (resp.dedupe === "hit") setState({ tag: "dedup", jobId: resp.job_id, filename: file.name })
        else navigate(`/job?id=${resp.job_id}`)
      } else {
        let code = "SERVER_ERROR"
        let message = "Something went wrong. Please try again."
        try {
          const body = JSON.parse(xhr.responseText) as { error?: { code?: string; message?: string } }
          code = body.error?.code ?? code
          message = body.error?.message ?? message
        } catch { /* unparseable — keep defaults */ }
        if (xhr.status === 413) code = "UPLOAD_TOO_LARGE"
        if (xhr.status === 415) code = "UNSUPPORTED_MEDIA"
        setState({ tag: "rejected", code, message })
      }
    }

    xhr.onerror = () => {
      currentXhr = null
      if (attempt < MAX_WAKING_ATTEMPTS) {
        const delay = Math.min(2 ** attempt * 1500, 15_000)
        setState({ tag: "waking", file, attempt })
        setTimeout(() => startUpload(file, attempt + 1), delay)
      } else {
        setState({ tag: "rejected", code: "NETWORK_ERROR", message: "Could not reach the server. Check your connection and try again." })
      }
    }

    xhr.onabort = () => { currentXhr = null }
    xhr.send(file)
  }

  // ---- drag & drop (document-level; removed on teardown via signal) ----
  document.addEventListener("dragenter", (e) => {
    e.preventDefault()
    dragCount++
    if (dragCount === 1) document.body.classList.add("drag-active")
  }, { signal })
  document.addEventListener("dragleave", () => {
    dragCount--
    if (dragCount === 0) document.body.classList.remove("drag-active")
  }, { signal })
  document.addEventListener("dragover", (e) => e.preventDefault(), { signal })
  document.addEventListener("drop", (e) => {
    e.preventDefault()
    dragCount = 0
    document.body.classList.remove("drag-active")
    const file = (e as DragEvent).dataTransfer?.files[0]
    if (file) {
      if (state.tag === "uploading") currentXhr?.abort()
      handleFile(file)
    }
  }, { signal })

  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0]
    if (file) handleFile(file)
    fileInput.value = "" // allow re-selecting the same file
  }, { signal })

  render()

  return () => {
    ac.abort() // removes every signal-bound listener
    currentXhr?.abort()
    fileInput.remove()
    document.body.classList.remove("drag-active")
  }
}
