import {
  getJob,
  postTranscode,
  postCancel,
  ApiError,
  type JobResponse,
  type JobError,
  type JobResults,
} from "./api";
import { watch, type StateFrame } from "./live";
import {
  failureMessage,
  friendlyCodec,
  friendlyContainer,
  readinessExplanation,
} from "./job-copy";
import {
  esc,
  humanBitrate,
  humanDuration,
  siteFooter,
  siteHeader,
} from "./render";
import {
  buildPicker,
  estimateSeconds,
  formatEstimate,
  type PickerRow,
} from "./presets";
import {
  mountCompletedResult,
  renderCompletedResult,
  type CompletedResultHandle,
} from "./results";

// Phase 5.4/5.5 — inspect/commit, then live progress and the player.

type View =
  | { tag: "loading" }
  | { tag: "inspecting" }
  | { tag: "awaiting"; job: JobResponse }
  | { tag: "progress" }
  | { tag: "done"; results: JobResults; expiresAt?: string | null }
  | { tag: "failed"; error?: JobError }
  | { tag: "cancelled" }
  | { tag: "expired" }
  | { tag: "notfound" }
  | { tag: "error"; message: string };

let appEl: HTMLElement; // set in mount()
let jobId: string | null = null;

let view: View = { tag: "loading" };
let rows: PickerRow[] = [];
let selected = new Set<string>();
let duration = 0;
let committing = false;
let commitError: string | null = null;
let captionsWanted = false;
let hasAudio = true;
let jobTitle = ""; // source filename, for the watch page header

// progress-view state
let presets: string[] = [];
let progress: Record<string, number> = {};
let mode: "live" | "polling" = "live";
let processingStatus: "queued" | "transcoding" = "queued";
let confirmingCancel = false;
let cancelling = false;
let cancelError: string | null = null;

// async drivers (one set at a time)
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let subsTimer: ReturnType<typeof setTimeout> | null = null;
let unwatch: (() => void) | null = null;
let completedResult: CompletedResultHandle | null = null;
let gen = 0; // invalidates in-flight load()s when the view is superseded
let errorAttempts = 0;
const MAX_ERROR_RETRIES = 5;

// ---- Load + route ---------------------------------------------------------

function cancelPoll(): void {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
  stopSubsWatch();
  stopWatch();
  gen++;
}

// Captions routinely outlive the ladder. Poll their status without rebuilding the completed-result page.
function stopSubsWatch(): void {
  if (subsTimer) clearTimeout(subsTimer);
  subsTimer = null;
}

function watchSubtitles(): void {
  stopSubsWatch();
  if (!jobId) return;
  const myGen = gen;
  subsTimer = setTimeout(async () => {
    if (myGen !== gen || !jobId) return;
    try {
      const job = await getJob(jobId);
      if (myGen !== gen) return;
      const subs = job.results?.subtitles;
      if (
        job.status === "done" &&
        job.results &&
        subs &&
        subs.status !== "processing"
      ) {
        view = {
          tag: "done",
          results: job.results,
          expiresAt: job.expires_at,
        };
        completedResult?.updateSubtitles(subs);
        stopSubsWatch();
        return;
      }
    } catch {
      // transient — keep watching
    }
    watchSubtitles();
  }, 4000);
}

async function load(): Promise<void> {
  if (!jobId)
    return setView({ tag: "error", message: "No job id in the URL." });
  cancelPoll();
  const myGen = gen;
  try {
    const job = await getJob(jobId);
    if (myGen !== gen) return; // superseded by a commit or a newer load
    errorAttempts = 0;
    route(job);
  } catch (e) {
    if (myGen !== gen) return;
    if (e instanceof ApiError && e.status === 404)
      return setView({ tag: "notfound" });
    if (e instanceof ApiError && e.status === 410)
      return setView({ tag: "expired" });
    if (errorAttempts >= MAX_ERROR_RETRIES) {
      return setView({
        tag: "error",
        message:
          "The backend may still be waking up. Refresh in a moment.",
      });
    }
    const delay = Math.min(2 ** errorAttempts * 1000, 8000);
    errorAttempts++;
    setView({
      tag: "error",
      message: "The backend may be waking up. Retrying…",
    });
    pollTimer = setTimeout(() => void load(), delay);
  }
}

function route(job: JobResponse): void {
  if (job.source_filename) jobTitle = job.source_filename;
  switch (job.status) {
    case "inspecting":
      setView({ tag: "inspecting" });
      pollTimer = setTimeout(() => void load(), 1200); // poll until ffprobe resolves
      break;
    case "awaiting_choice":
      initAwaiting(job);
      break;
    case "queued":
    case "transcoding":
      startProgress(job);
      break;
    case "done":
      if (job.results)
        setView({
          tag: "done",
          results: job.results,
          expiresAt: job.expires_at,
        });
      else
        setView({
          tag: "error",
          message: "This job is done but its results are unavailable.",
        });
      break;
    case "failed":
      setView({ tag: "failed", error: job.error });
      break;
    case "cancelled":
      setView({ tag: "cancelled" });
      break;
    case "expired":
      setView({ tag: "expired" });
      break;
  }
}

function initAwaiting(job: JobResponse): void {
  rows = buildPicker(job.recommended_presets ?? [], job.source?.height ?? 0);
  selected = new Set(rows.filter((r) => r.checked).map((r) => r.preset));
  duration = job.source?.duration ?? 0;
  hasAudio = job.source?.has_audio !== false;
  captionsWanted = false;
  commitError = null;
  committing = false;
  setView({ tag: "awaiting", job });
}

// ---- Live progress --------------------------------------------------------

function startProgress(job: JobResponse): void {
  presets = job.presets ?? [];
  progress = job.progress ?? {};
  mode = "live";
  processingStatus = job.status === "transcoding" ? "transcoding" : "queued";
  confirmingCancel = false;
  cancelling = false;
  cancelError = null;
  setView({ tag: "progress" });
  startWatch();
}

async function doCancel(): Promise<void> {
  if (!jobId || cancelling) return;
  cancelling = true;
  cancelError = null;
  render();
  document.querySelector<HTMLElement>(".cancel-confirm")?.focus();
  try {
    const result = await postCancel(jobId);
    if (result.status === "cancelled") {
      stopWatch();
      setView({ tag: "cancelled" });
    } else {
      void load();
    }
  } catch (e) {
    cancelling = false;
    if (e instanceof ApiError && e.status === 409) return load();
    cancelError =
      "Tideo couldn’t cancel this job. Processing is still running. Try again.";
    render();
    document.getElementById("cancel-confirm-btn")?.focus();
  }
}

function startWatch(): void {
  stopWatch();
  if (!jobId) return;
  unwatch = watch(jobId, {
    onSnapshot: (f) => {
      if (f.presets?.length) presets = f.presets;
      if (f.status === "queued" || f.status === "transcoding")
        processingStatus = f.status;
      progress = { ...progress, ...f.progress };
      if (view.tag === "progress") updateBars();
    },
    onProgress: (f) => {
      processingStatus = "transcoding";
      progress[f.preset] = f.percent;
      if (view.tag === "progress") updateBars();
    },
    onState: (f) => onTerminal(f),
    onMode: (m) => {
      mode = m;
      if (view.tag === "progress") render(); // pill toggles infrequently
    },
  });
}

function stopWatch(): void {
  unwatch?.();
  unwatch = null;
}

function onTerminal(f: StateFrame): void {
  stopWatch();
  switch (f.status) {
    case "done":
      if (f.results) setView({ tag: "done", results: f.results });
      else {
        console.warn("done frame without results; refetching", jobId);
        void load();
      }
      break;
    case "failed":
      setView({ tag: "failed", error: f.error });
      break;
    case "cancelled":
      setView({ tag: "cancelled" });
      break;
    case "expired":
      setView({ tag: "expired" });
      break;
    default:
      void load();
  }
}

// ---- Commit ---------------------------------------------------------------

async function commit(): Promise<void> {
  if (!jobId || committing || selected.size === 0) return;
  cancelPoll();
  committing = true;
  commitError = null;
  render();
  try {
    await postTranscode(jobId, {
      presets: [...selected],
      subtitles: captionsWanted && hasAudio,
    });
    void load(); // refetch → routes into the progress view
  } catch (e) {
    committing = false;
    if (e instanceof ApiError && e.status === 409) return load();
    commitError = commitFailureMessage(e);
    render();
    document.querySelector<HTMLElement>(".commit-error")?.focus();
  }
}

function commitFailureMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) {
    return "Those output choices are no longer available. Refresh the page and choose again.";
  }
  return "Tideo couldn’t start transcoding. Your choices are preserved. Try again.";
}

// ---- Render ---------------------------------------------------------------

function setView(next: View): void {
  view = next;
  render();
  if (
    next.tag === "failed" ||
    next.tag === "cancelled" ||
    next.tag === "expired" ||
    next.tag === "notfound"
  ) {
    appEl
      .querySelector<HTMLElement>(".inspect-card--terminal .inspect-title")
      ?.focus();
  }
}

function render(): void {
  if (completedResult) {
    completedResult.destroy();
    completedResult = null;
  }
  appEl.innerHTML = `
    ${siteHeader()}
    <main id="main-content" class="job-main">${card()}</main>
    ${siteFooter()}
  `;
  bind();
  if (view.tag === "done" && jobId) {
    completedResult = mountCompletedResult(appEl, {
      jobId,
      title: jobTitle || jobId,
      results: view.results,
      expiresAt: view.expiresAt,
    });
    if (view.results.subtitles?.status === "processing") watchSubtitles();
    else stopSubsWatch();
  } else {
    stopSubsWatch();
  }
}

function card(): string {
  switch (view.tag) {
    case "loading":
    case "inspecting":
      return cardInspecting();
    case "awaiting":
      return cardAwaiting(view.job);
    case "progress":
      return cardProgress();
    case "done":
      return renderCompletedResult({
        jobId: jobId ?? "",
        title: jobTitle || jobId || "Your video",
        results: view.results,
        expiresAt: view.expiresAt,
      });
    case "failed":
      return cardFailed(view.error);
    case "cancelled":
      return cardMessage(
        "Job cancelled",
        "Transcoding stopped and temporary work from this job may have been discarded.",
        "/upload",
        "Upload video",
        { href: "/history", label: "My videos" },
      );
    case "expired":
      return cardMessage(
        "Outputs expired",
        "This temporary output reached its retention limit and has been removed. Upload the source again to recreate it.",
        "/upload",
        "Upload video",
        { href: "/history", label: "My videos" },
      );
    case "notfound":
      return cardMessage(
        "Job not found",
        "This link is incorrect, expired, or belongs to another browser session.",
        "/history",
        "My videos",
        { href: "/upload", label: "Upload video" },
      );
    case "error":
      return cardLoadError(view.message);
  }
}

// Skeleton matches the resolved layout to avoid shift.
function cardInspecting(): string {
  return `
    <div class="inspect-card" aria-busy="true" aria-label="Inspecting your video">
      <h1 class="sr-only">Inspecting video</h1>
      <div class="inspect-head">
        <div class="skeleton sk-title"></div>
        <div class="skeleton sk-badge"></div>
      </div>
      <div class="spec-grid">
        ${'<div class="spec-row"><div class="skeleton sk-key"></div><div class="skeleton sk-val"></div></div>'.repeat(5)}
      </div>
      <div class="picker">
        ${'<div class="picker-row"><div class="skeleton sk-pick"></div></div>'.repeat(4)}
      </div>
      <div class="skeleton sk-btn"></div>
    </div>
  `;
}

function cardAwaiting(job: JobResponse): string {
  const s = job.source!;
  const safe = job.web_safe === true;
  const filename = job.source_filename
    ? esc(job.source_filename)
    : "your video";
  return `
    <div class="inspect-card" ${committing ? 'aria-busy="true"' : ""}>
      <div class="inspect-head">
        <h1 class="inspect-title" title="${filename}">${filename}</h1>
        <span class="status-badge ${safe ? "status-badge--success" : "status-badge--warning"}">
          ${safe ? "Web-ready" : "Web copy needed"}
        </span>
      </div>

      <div class="spec-grid">
        ${specRow("Format", esc(friendlyContainer(s.container)))}
        ${specRow("Video", esc(friendlyCodec(s.video_codec)))}
        ${specRow("Audio", s.has_audio ? esc(friendlyCodec(s.audio_codec)) : "No audio")}
        ${specRow("Resolution", `${s.width}×${s.height}`)}
        ${specRow("Duration", humanDuration(s.duration))}
        ${specRow("Bitrate", humanBitrate(s.bitrate))}
      </div>

      <div class="source-readiness ${safe ? "source-readiness--ready" : "source-readiness--convert"}">
        <strong>${safe ? "Ready for the web" : "Tideo will create a web-ready copy"}</strong>
        <p>${readinessExplanation(job)}</p>
      </div>

      <fieldset class="picker" aria-describedby="picker-help">
        <legend class="picker-legend">Choose outputs</legend>
        <p class="picker-help" id="picker-help">Select the playback sizes Tideo should create. Unavailable sizes remain visible so the source limit is clear.</p>
        ${rows.map(pickerRowHtml).join("")}
      </fieldset>

      <label class="toggle-row ${hasAudio ? "" : "toggle-disabled"}">
        <input type="checkbox" id="captions-toggle" ${captionsWanted ? "checked" : ""} ${hasAudio ? "" : "disabled"} />
        <span class="captions-copy">
          <strong>Generate captions</strong>
          <span>${hasAudio ? "Creates a WebVTT caption track from the source audio." : "No audio track. Captions are unavailable for this video."}</span>
        </span>
      </label>

      ${commitError ? `<p class="error-message commit-error" role="alert" tabindex="-1">${esc(commitError)}</p>` : ""}
      <p class="sr-only" role="status" aria-live="polite">${committing ? "Starting transcoding" : ""}</p>

      <div class="commit-row">
        <span class="estimate" id="estimate" aria-live="polite">${estimateText()}</span>
        <button class="btn btn-primary" id="commit-btn" type="button"
                ${selected.size === 0 || committing ? "disabled" : ""}>
          ${committing ? "Starting…" : "Start transcoding"}
        </button>
      </div>
    </div>
  `;
}

function pickerRowHtml(r: PickerRow): string {
  const checked = selected.has(r.preset) ? "checked" : "";
  const disabled = r.available ? "" : "disabled";
  return `
    <label class="picker-row ${r.available ? "" : "picker-row--disabled"}">
      <input type="checkbox" data-preset="${r.preset}" ${checked} ${disabled} />
      <span class="picker-label">${r.label}</span>
      <span class="picker-res">${r.resolution}</span>
      ${r.reason ? `<span class="picker-reason">${esc(r.reason)}</span>` : ""}
    </label>
  `;
}

function specRow(key: string, val: string): string {
  return `<div class="spec-row"><span class="spec-key">${key}</span><span class="spec-val">${val}</span></div>`;
}

function estimateText(): string {
  if (selected.size === 0) return "Select at least one quality";
  return `Rough estimate: ${formatEstimate(estimateSeconds([...selected], duration))}`;
}

function cardProgress(): string {
  const allDone =
    presets.length > 0 && presets.every((p) => (progress[p] ?? 0) >= 100);
  const phase = allDone
    ? "Packaging outputs"
    : processingStatus === "queued"
      ? "Queued"
      : "Transcoding";
  const filename = esc(jobTitle || "Your video");
  return `
    <div class="inspect-card progress-card">
      <div class="processing-head">
        <div>
          <p class="processing-phase">${phase}</p>
          <h1 class="inspect-title" title="${filename}">${filename}</h1>
        </div>
        <span class="status-badge ${allDone ? "status-badge--success" : "status-badge--warning"}">${allDone ? "Finalizing" : "In progress"}</span>
      </div>
      ${mode === "polling" ? `<p class="connection-notice" role="status">Live updates paused. Checking automatically.</p>` : ""}
      <p class="processing-guidance">You can leave this page and return from <a href="/history">My videos</a>. Processing continues in the background.</p>
      <div class="bars">${presets.map(progressBar).join("") || '<p class="term-msg">Queued…</p>'}</div>
      <p class="progress-status" id="progress-status" role="status" aria-live="polite" aria-atomic="true">${statusLine()}</p>
      ${
        confirmingCancel
          ? `<div class="cancel-confirm" role="group" aria-labelledby="cancel-title" aria-busy="${cancelling}" tabindex="-1">
             <div>
               <h2 id="cancel-title">Cancel transcoding?</h2>
               <p>Completed temporary work may be discarded. This cannot be undone.</p>
             </div>
             ${cancelError ? `<p class="error-message cancel-error" role="alert">${esc(cancelError)}</p>` : ""}
             <div class="cancel-actions">
               <button class="btn btn-danger" id="cancel-confirm-btn" type="button" ${cancelling ? "disabled" : ""}>${cancelling ? "Cancelling…" : "Cancel job"}</button>
               <button class="btn btn-ghost" id="cancel-keep-btn" type="button" ${cancelling ? "disabled" : ""}>Keep processing</button>
             </div>
           </div>`
          : `<button class="btn btn-ghost cancel-trigger" id="cancel-btn" type="button">Cancel job</button>`
      }
    </div>
  `;
}

function statusLine(): string {
  const total = presets.length;
  if (total === 0) return "Waiting for a worker to start this job.";
  const done = presets.filter((p) => (progress[p] ?? 0) >= 100).length;
  if (total > 0 && done === total)
    return "Packaging the stream and generating previews.";
  return `${done} of ${total} renditions complete`;
}

function progressBar(preset: string): string {
  const pct = progressPercent(progress[preset]);
  const complete = pct >= 100;
  return `
    <div class="bar-row ${complete ? "is-complete" : ""}" data-bar="${esc(preset)}">
      <span class="bar-label">${esc(preset)}</span>
      <div class="progress-bar-track" role="progressbar" aria-label="${esc(preset)} transcoding progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-valuetext="${complete ? "Complete" : `${pct}%`}"><div class="progress-bar-fill" style="transform: scaleX(${pct / 100})"></div></div>
      <span class="bar-value"><span class="bar-pct">${pct}%</span><span class="bar-complete">${complete ? "Complete" : ""}</span></span>
    </div>
  `;
}

function progressPercent(value: number | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, Math.round(value)));
}

function updateBars(): void {
  for (const p of presets) {
    const row = appEl.querySelector(`[data-bar="${CSS.escape(p)}"]`);
    if (!row) return render(); // bar set changed — rebuild
    const pct = progressPercent(progress[p]);
    const complete = pct >= 100;
    const track = row.querySelector<HTMLElement>(".progress-bar-track")!;
    track.setAttribute("aria-valuenow", String(pct));
    track.setAttribute("aria-valuetext", complete ? "Complete" : `${pct}%`);
    track.querySelector<HTMLElement>(".progress-bar-fill")!.style.transform =
      `scaleX(${pct / 100})`;
    row.querySelector<HTMLElement>(".bar-pct")!.textContent = `${pct}%`;
    row.querySelector<HTMLElement>(".bar-complete")!.textContent = complete
      ? "Complete"
      : "";
    row.classList.toggle("is-complete", complete);
  }
  const status = document.getElementById("progress-status");
  if (status) status.textContent = statusLine();
  const allDone =
    presets.length > 0 && presets.every((p) => (progress[p] ?? 0) >= 100);
  const phase = appEl.querySelector(".processing-phase");
  if (phase)
    phase.textContent = allDone
      ? "Packaging outputs"
      : processingStatus === "queued"
        ? "Queued"
        : "Transcoding";
  const badge = appEl.querySelector(".progress-card .status-badge");
  if (badge && allDone) {
    badge.textContent = "Finalizing";
    badge.classList.remove("status-badge--warning");
    badge.classList.add("status-badge--success");
  }
}

function cardFailed(error?: JobError): string {
  const inspecting = error?.stage === "inspect";
  const title = inspecting
    ? "Couldn’t inspect this video"
    : "Couldn’t finish transcoding";
  return `
    <div class="inspect-card inspect-card--terminal">
      <h1 class="inspect-title" tabindex="-1">${title}</h1>
      <p class="term-msg">${failureMessage(error)}</p>
      ${terminalActions("/upload", "Upload video", { href: "/history", label: "My videos" })}
    </div>
  `;
}

function terminalActions(
  href: string,
  label: string,
  secondary?: { href: string; label: string },
): string {
  return `<div class="terminal-actions">
    <a href="${href}" class="btn btn-primary">${label}</a>
    ${secondary ? `<a href="${secondary.href}" class="btn btn-ghost">${secondary.label}</a>` : ""}
  </div>`;
}

function cardMessage(
  title: string,
  msg: string,
  href = "/upload",
  label = "Upload video",
  secondary?: { href: string; label: string },
): string {
  return `
    <div class="inspect-card inspect-card--terminal">
      <h1 class="inspect-title" tabindex="-1">${esc(title)}</h1>
      <p class="term-msg">${esc(msg)}</p>
      ${terminalActions(href, label, secondary)}
    </div>
  `;
}

function cardLoadError(message: string): string {
  return `
    <div class="inspect-card inspect-card--terminal">
      <h1 class="inspect-title" tabindex="-1">Tideo is unavailable</h1>
      <p class="term-msg">${esc(message)}</p>
      <div class="terminal-actions">
        <button class="btn btn-primary" id="retry-load" type="button">Try again</button>
        <a href="/history" class="btn btn-ghost">My videos</a>
      </div>
    </div>
  `;
}

// ---- Bind -----------------------------------------------------------------

function bind(): void {
  appEl
    .querySelectorAll<HTMLInputElement>("input[data-preset]")
    .forEach((box) => {
      box.addEventListener("change", () => {
        const preset = box.dataset.preset!;
        if (box.checked) selected.add(preset);
        else selected.delete(preset);
        refreshCommit();
      });
    });
  document
    .getElementById("captions-toggle")
    ?.addEventListener("change", (e) => {
      captionsWanted = (e.currentTarget as HTMLInputElement).checked;
    });
  document
    .getElementById("commit-btn")
    ?.addEventListener("click", () => void commit());

  document.getElementById("cancel-btn")?.addEventListener("click", () => {
    confirmingCancel = true;
    cancelError = null;
    render();
    document.getElementById("cancel-confirm-btn")?.focus();
  });
  document.getElementById("cancel-keep-btn")?.addEventListener("click", () => {
    confirmingCancel = false;
    cancelError = null;
    render();
    document.getElementById("cancel-btn")?.focus();
  });
  document
    .getElementById("cancel-confirm-btn")
    ?.addEventListener("click", () => void doCancel());
  document.getElementById("retry-load")?.addEventListener("click", () => {
    errorAttempts = 0;
    setView({ tag: "loading" });
    void load();
  });
}

// Targeted update so toggling a checkbox doesn't re-render the picker and drop focus.
function refreshCommit(): void {
  const est = document.getElementById("estimate");
  if (est) est.textContent = estimateText();
  const btn = document.getElementById("commit-btn") as HTMLButtonElement | null;
  if (btn) btn.disabled = selected.size === 0 || committing;
}

// ---- Mount ----------------------------------------------------------------

export function mount(root: HTMLElement, query: URLSearchParams): () => void {
  appEl = root;
  jobId = query.get("id");
  // reset per-mount state so navigating back to a job starts clean
  view = { tag: "loading" };
  rows = [];
  selected = new Set();
  duration = 0;
  committing = false;
  commitError = null;
  captionsWanted = false;
  hasAudio = true;
  jobTitle = "";
  presets = [];
  progress = {};
  mode = "live";
  processingStatus = "queued";
  confirmingCancel = false;
  cancelling = false;
  cancelError = null;
  pollTimer = null;
  subsTimer = null;
  unwatch = null;
  completedResult = null;
  errorAttempts = 0;
  // gen is NOT reset — it stays monotonic across mounts so a stale in-flight load() can't write into a remount's DOM

  render();
  void load();

  return () => {
    cancelPoll();
    if (completedResult) {
      completedResult.destroy();
      completedResult = null;
    }
  };
}
