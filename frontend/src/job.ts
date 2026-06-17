import "./style.css"
import { getJob, postTranscode, ApiError, type JobResponse, type JobError } from "./api"
import { esc, humanDuration, humanBitrate } from "./render"
import { buildPicker, estimateSeconds, type PickerRow } from "./presets"

// Phase 5.4 — inspect/commit screen. Progress/player states are Phase 5.5.

type View =
  | { tag: "loading" }
  | { tag: "inspecting" }
  | { tag: "awaiting"; job: JobResponse }
  | { tag: "failed"; error?: JobError }
  | { tag: "seam"; status: string }
  | { tag: "notfound" }
  | { tag: "error"; message: string }

const appEl = document.getElementById("app")!
const jobId = new URLSearchParams(location.search).get("id")

let view: View = { tag: "loading" }
let rows: PickerRow[] = []
let selected = new Set<string>()
let duration = 0
let committing = false
let commitError: string | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null
let gen = 0 // invalidates in-flight load()s when the view is superseded
let errorAttempts = 0
const MAX_ERROR_RETRIES = 5

// ---- Load + route ---------------------------------------------------------

function cancelPoll(): void {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  gen++
}

async function load(): Promise<void> {
  if (!jobId) return setView({ tag: "error", message: "No job id in the URL." })
  cancelPoll()
  const myGen = gen
  try {
    const job = await getJob(jobId)
    if (myGen !== gen) return // superseded by a commit or a newer load
    errorAttempts = 0
    route(job)
  } catch (e) {
    if (myGen !== gen) return
    if (e instanceof ApiError && e.status === 404) return setView({ tag: "notfound" })
    if (e instanceof ApiError && e.status === 410) return setView({ tag: "seam", status: "expired" })
    if (errorAttempts >= MAX_ERROR_RETRIES) {
      return setView({ tag: "error", message: "Couldn’t load this job. Check your connection and refresh." })
    }
    const delay = Math.min(2 ** errorAttempts * 1000, 8000)
    errorAttempts++
    setView({ tag: "error", message: "Couldn’t load this job. Retrying…" })
    pollTimer = setTimeout(() => void load(), delay)
  }
}

function route(job: JobResponse): void {
  switch (job.status) {
    case "inspecting":
      setView({ tag: "inspecting" })
      pollTimer = setTimeout(() => void load(), 1200) // poll until ffprobe resolves
      break
    case "awaiting_choice":
      initAwaiting(job)
      break
    case "failed":
      setView({ tag: "failed", error: job.error })
      break
    default: // queued / transcoding / done / cancelled / expired
      setView({ tag: "seam", status: job.status })
  }
}

function initAwaiting(job: JobResponse): void {
  rows = buildPicker(job.recommended_presets ?? [], job.source?.height ?? 0)
  selected = new Set(rows.filter((r) => r.checked).map((r) => r.preset))
  duration = job.source?.duration ?? 0
  commitError = null
  committing = false
  setView({ tag: "awaiting", job })
}

// ---- Commit ---------------------------------------------------------------

async function commit(): Promise<void> {
  if (!jobId || committing || selected.size === 0) return
  cancelPoll()
  committing = true
  commitError = null
  render()
  try {
    await postTranscode(jobId, { presets: [...selected], subtitles: false })
    setView({ tag: "seam", status: "queued" })
  } catch (e) {
    committing = false
    if (e instanceof ApiError && e.status === 409) return load() // state moved on — refetch
    commitError =
      e instanceof ApiError && e.status === 422
        ? e.message
        : "Couldn't start transcoding. Please try again."
    render()
  }
}

// ---- Render ---------------------------------------------------------------

function setView(next: View): void {
  view = next
  render()
}

function render(): void {
  appEl.innerHTML = `
    <header class="site-header"><a href="/" class="wordmark">tideo</a></header>
    <main class="job-main">${card()}</main>
  `
  bind()
}

function card(): string {
  switch (view.tag) {
    case "loading":
    case "inspecting":
      return cardInspecting()
    case "awaiting":
      return cardAwaiting(view.job)
    case "failed":
      return cardFailed(view.error)
    case "seam":
      return cardSeam(view.status)
    case "notfound":
      return cardMessage("Job not found", "This job doesn’t exist or was never created.")
    case "error":
      return cardMessage("Something went wrong", view.message)
  }
}

// Skeleton matches the resolved layout to avoid shift.
function cardInspecting(): string {
  return `
    <div class="inspect-card" aria-busy="true" aria-label="Inspecting your video">
      <div class="inspect-head">
        <div class="sk sk-title"></div>
        <div class="sk sk-badge"></div>
      </div>
      <div class="spec-grid">
        ${"<div class=\"spec-row\"><div class=\"sk sk-key\"></div><div class=\"sk sk-val\"></div></div>".repeat(5)}
      </div>
      <div class="picker">
        ${"<div class=\"picker-row\"><div class=\"sk sk-pick\"></div></div>".repeat(4)}
      </div>
      <div class="sk sk-btn"></div>
    </div>
  `
}

function cardAwaiting(job: JobResponse): string {
  const s = job.source!
  const safe = job.web_safe === true
  const badgeReason = job.web_safe_reason ? esc(job.web_safe_reason) : "already H.264/AAC in MP4"
  const filename = job.source_filename ? esc(job.source_filename) : "your video"
  return `
    <div class="inspect-card">
      <div class="inspect-head">
        <h1 class="inspect-title" title="${filename}">${filename}</h1>
        <span class="badge ${safe ? "badge-ok" : "badge-warn"}"
              title="${safe ? "Web-ready — fast remux" : badgeReason}">
          ${safe ? "web-ready" : "needs re-encode"}
        </span>
      </div>

      <div class="spec-grid">
        ${specRow("Container", esc(s.container))}
        ${specRow("Video", esc(s.video_codec ?? "—"))}
        ${specRow("Audio", s.has_audio ? esc(s.audio_codec ?? "—") : "none")}
        ${specRow("Resolution", `${s.width}×${s.height}`)}
        ${specRow("Duration", humanDuration(s.duration))}
        ${specRow("Bitrate", humanBitrate(s.bitrate))}
      </div>

      <fieldset class="picker">
        <legend class="picker-legend">Output qualities</legend>
        ${rows.map(pickerRowHtml).join("")}
      </fieldset>

      <label class="toggle-row toggle-disabled" title="Captions arrive in a later phase">
        <input type="checkbox" disabled />
        <span>Generate captions</span>
        <span class="toggle-note">coming soon</span>
      </label>

      ${commitError ? `<p class="commit-error">${esc(commitError)}</p>` : ""}

      <div class="commit-row">
        <span class="estimate" id="estimate">${estimateText()}</span>
        <button class="btn btn-primary" id="commit-btn" type="button"
                ${selected.size === 0 || committing ? "disabled" : ""}>
          ${committing ? "Starting…" : "Start transcoding →"}
        </button>
      </div>
    </div>
  `
}

function pickerRowHtml(r: PickerRow): string {
  const checked = selected.has(r.preset) ? "checked" : ""
  const disabled = r.available ? "" : "disabled"
  return `
    <label class="picker-row ${r.available ? "" : "picker-row--disabled"}">
      <input type="checkbox" data-preset="${r.preset}" ${checked} ${disabled} />
      <span class="picker-label">${r.label}</span>
      <span class="picker-res">${r.resolution}</span>
      ${r.reason ? `<span class="picker-reason">${esc(r.reason)}</span>` : ""}
    </label>
  `
}

function specRow(key: string, val: string): string {
  return `<div class="spec-row"><span class="spec-key">${key}</span><span class="spec-val">${val}</span></div>`
}

function estimateText(): string {
  if (selected.size === 0) return "Select at least one quality"
  return `~${humanDuration(estimateSeconds([...selected], duration))} to transcode (estimate)`
}

function cardFailed(error?: JobError): string {
  const code = error?.code ?? "FAILED"
  const stage = error?.stage ? ` · ${esc(error.stage)}` : ""
  return `
    <div class="inspect-card inspect-card--terminal">
      <h1 class="inspect-title">Couldn’t process this video</h1>
      <p class="term-code">${esc(code)}${stage}</p>
      <p class="term-msg">${esc(error?.message ?? "The file couldn’t be inspected.")}</p>
      <a href="/" class="btn btn-primary">Upload another file</a>
    </div>
  `
}

function cardSeam(status: string): string {
  return `
    <div class="inspect-card inspect-card--terminal">
      <h1 class="inspect-title">Job is ${esc(status)}</h1>
      <p class="term-msg">The live progress and player screen lands in Phase 5.5.</p>
      <a href="/" class="btn btn-ghost">Upload another file</a>
    </div>
  `
}

function cardMessage(title: string, msg: string): string {
  return `
    <div class="inspect-card inspect-card--terminal">
      <h1 class="inspect-title">${esc(title)}</h1>
      <p class="term-msg">${esc(msg)}</p>
      <a href="/" class="btn btn-primary">Back to upload</a>
    </div>
  `
}

// ---- Bind -----------------------------------------------------------------

function bind(): void {
  appEl.querySelectorAll<HTMLInputElement>("input[data-preset]").forEach((box) => {
    box.addEventListener("change", () => {
      const preset = box.dataset.preset!
      if (box.checked) selected.add(preset)
      else selected.delete(preset)
      refreshCommit()
    })
  })
  document.getElementById("commit-btn")?.addEventListener("click", () => void commit())
}

// Targeted update so toggling a checkbox doesn't re-render the picker and drop focus.
function refreshCommit(): void {
  const est = document.getElementById("estimate")
  if (est) est.textContent = estimateText()
  const btn = document.getElementById("commit-btn") as HTMLButtonElement | null
  if (btn) btn.disabled = selected.size === 0 || committing
}

// ---- Boot -----------------------------------------------------------------

void load()
