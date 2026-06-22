import {
  getJob,
  getManifest,
  postTranscode,
  postCancel,
  ApiError,
  apiBase,
  type JobResponse,
  type JobError,
  type JobResults,
  type Manifest,
} from "./api";
import { watch, type StateFrame } from "./live";
import { mountPlayer, type PlayerHandle } from "./player";
import { esc, humanDuration, humanBitrate, siteHeader } from "./render";
import { buildPicker, estimateSeconds, type PickerRow } from "./presets";
import { loadStoryboard, spriteUrl } from "./sprite";

// Phase 5.4/5.5 — inspect/commit, then live progress and the player.

type View =
  | { tag: "loading" }
  | { tag: "inspecting" }
  | { tag: "awaiting"; job: JobResponse }
  | { tag: "progress" }
  | { tag: "done"; results: JobResults }
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
let confirmingCancel = false;

// async drivers (one set at a time)
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let subsTimer: ReturnType<typeof setTimeout> | null = null;
let unwatch: (() => void) | null = null;
let player: PlayerHandle | null = null;
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

// Captions routinely outlive the ladder. When `done` arrives with subtitles still processing, poll until
// the result is terminal, then re-render so the player remounts and picks up the rewritten master (CC button).
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
        setView({ tag: "done", results: job.results });
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
      if (job.results) setView({ tag: "done", results: job.results });
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
  confirmingCancel = false;
  setView({ tag: "progress" });
  startWatch();
}

async function doCancel(): Promise<void> {
  if (!jobId) return;
  try {
    await postCancel(jobId);
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) return load();
  }
}

function startWatch(): void {
  stopWatch();
  if (!jobId) return;
  unwatch = watch(jobId, {
    onSnapshot: (f) => {
      if (f.presets?.length) presets = f.presets;
      progress = { ...progress, ...f.progress };
      if (view.tag === "progress") updateBars();
    },
    onProgress: (f) => {
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
    commitError =
      e instanceof ApiError && e.status === 422
        ? e.message
        : "Couldn't start transcoding. Please try again.";
    render();
  }
}

// ---- Render ---------------------------------------------------------------

function setView(next: View): void {
  view = next;
  render();
}

function render(): void {
  if (player) {
    player.destroy();
    player = null;
  }
  appEl.innerHTML = `
    ${siteHeader()}
    <main class="job-main">${card()}</main>
  `;
  bind();
  if (view.tag === "done") {
    mountDonePlayer(view.results);
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
      return cardDone(view.results);
    case "failed":
      return cardFailed(view.error);
    case "cancelled":
      return cardMessage(
        "Job cancelled",
        "This job was cancelled. You can upload it again.",
      );
    case "expired":
      return cardMessage(
        "Outputs expired",
        "Demo outputs live for a limited time and have been cleaned up. Upload again to re-create them.",
      );
    case "notfound":
      return cardMessage(
        "Job not found",
        "This job doesn’t exist or was never created.",
      );
    case "error":
      return cardMessage("Something went wrong", view.message);
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
        ${'<div class="spec-row"><div class="sk sk-key"></div><div class="sk sk-val"></div></div>'.repeat(5)}
      </div>
      <div class="picker">
        ${'<div class="picker-row"><div class="sk sk-pick"></div></div>'.repeat(4)}
      </div>
      <div class="sk sk-btn"></div>
    </div>
  `;
}

function cardAwaiting(job: JobResponse): string {
  const s = job.source!;
  const safe = job.web_safe === true;
  const badgeReason = job.web_safe_reason
    ? esc(job.web_safe_reason)
    : "already H.264/AAC in MP4";
  const filename = job.source_filename
    ? esc(job.source_filename)
    : "your video";
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

      <label class="toggle-row ${hasAudio ? "" : "toggle-disabled"}"
             title="${hasAudio ? "Transcribe speech to a WebVTT caption track" : "No audio stream — nothing to transcribe"}">
        <input type="checkbox" id="captions-toggle" ${captionsWanted ? "checked" : ""} ${hasAudio ? "" : "disabled"} />
        <span>Generate captions</span>
        ${hasAudio ? "" : `<span class="toggle-note">no audio</span>`}
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
  return `~${humanDuration(estimateSeconds([...selected], duration))} to transcode (estimate)`;
}

function cardProgress(): string {
  const allDone =
    presets.length > 0 && presets.every((p) => (progress[p] ?? 0) >= 100);
  return `
    <div class="inspect-card progress-card">
      <div class="inspect-head">
        <h1 class="inspect-title">${allDone ? "Finalizing…" : "Transcoding…"}</h1>
        ${mode === "polling" ? `<span class="mode-pill">live updates paused — retrying</span>` : ""}
      </div>
      <div class="bars">${presets.map(progressBar).join("") || '<p class="term-msg">Queued…</p>'}</div>
      <p class="progress-status" id="progress-status">${statusLine()}</p>
      ${
        confirmingCancel
          ? `<div class="cancel-confirm">
             <button class="btn btn-danger" id="cancel-confirm-btn" type="button">Confirm cancel</button>
             <button class="btn btn-ghost" id="cancel-keep-btn" type="button">Keep going</button>
           </div>`
          : `<button class="btn btn-ghost" id="cancel-btn" type="button">Cancel</button>`
      }
    </div>
  `;
}

function statusLine(): string {
  const total = presets.length;
  const done = presets.filter((p) => (progress[p] ?? 0) >= 100).length;
  if (total > 0 && done === total)
    return "Packaging and generating thumbnails…";
  return `${done} of ${total} renditions complete`;
}

function progressBar(preset: string): string {
  const pct = Math.round(progress[preset] ?? 0);
  return `
    <div class="bar-row" data-bar="${esc(preset)}">
      <span class="bar-label">${esc(preset)}</span>
      <div class="progress-bar-track"><div class="progress-bar-fill" style="width:${pct}%"></div></div>
      <span class="bar-pct">${pct}%</span>
    </div>
  `;
}

function updateBars(): void {
  for (const p of presets) {
    const row = appEl.querySelector(`[data-bar="${CSS.escape(p)}"]`);
    if (!row) return render(); // bar set changed — rebuild
    const pct = Math.round(progress[p] ?? 0);
    row.querySelector<HTMLElement>(".progress-bar-fill")!.style.width =
      `${pct}%`;
    row.querySelector<HTMLElement>(".bar-pct")!.textContent = `${pct}%`;
  }
  const status = document.getElementById("progress-status");
  if (status) status.textContent = statusLine();
  const allDone =
    presets.length > 0 && presets.every((p) => (progress[p] ?? 0) >= 100);
  const title = appEl.querySelector(".progress-card .inspect-title");
  if (title && allDone) title.textContent = "Finalizing…";
}

function captionNote(results: JobResults): string {
  const subs = results.subtitles;
  if (!subs) return "";
  const text =
    subs.status === "processing"
      ? "Captions: generating…"
      : subs.status === "ready"
        ? "Captions: ready — toggle CC in the player"
        : subs.status === "none"
          ? "Captions: no speech to transcribe"
          : "Captions: unavailable";
  return `<p class="caption-note caption-${subs.status}">${text}</p>`;
}

function cardDone(results: JobResults): string {
  const base = apiBase();
  const title = esc(jobTitle || jobId || "your video");
  const n = results.presets?.length ?? 0;
  const meta = [
    results.duration != null ? humanDuration(results.duration) : "",
    n ? `${n} rendition${n === 1 ? "" : "s"}` : "",
    "adaptive HLS",
  ]
    .filter(Boolean)
    .join("  ·  ");
  return `
    <div class="watch">
      <section class="watch-stage">
        <div class="player-mount" id="player-mount"></div>
        <div class="watch-overlay">
          <p class="watch-eyebrow">Now playing</p>
          <h1 class="watch-title">${title}</h1>
        </div>
        <button class="watch-scroll" id="watch-scroll" type="button" aria-label="See details">details ↓</button>
      </section>
      <section class="watch-detail" id="watch-detail">
        <div class="watch-detail-inner">
          <div class="watch-head">
            <div>
              <h2 class="watch-name">${title}</h2>
              <p class="watch-meta">${esc(meta)}<span class="watch-chip">ready</span></p>
            </div>
            <div class="watch-actions">
              <a class="btn btn-primary" href="${base + results.web_mp4}" download>Download MP4</a>
              <button class="btn btn-ghost" id="copy-master" type="button">Copy stream URL</button>
              <a class="btn btn-ghost" href="/upload">New upload</a>
            </div>
          </div>
          ${captionNote(results)}
          <div class="ladder" id="ladder">
            <div class="ladder-head">Rendition ladder</div>
            <div class="ladder-rows" id="ladder-rows"><div class="ladder-loading">reading manifest…</div></div>
          </div>
          <details class="embed-block">
            <summary>Embed snippet</summary>
            <pre class="embed-code">${esc(embedSnippet(base + results.playlist))}</pre>
            <button class="btn btn-ghost" id="copy-embed" type="button">Copy snippet</button>
          </details>
        </div>
      </section>
    </div>
  `;
}

function renderLadder(m: Manifest): string {
  const rungs = [...m.renditions].sort((a, b) => b.bandwidth - a.bandwidth);
  const max = Math.max(1, ...rungs.map((r) => r.bandwidth));
  return rungs
    .map((r) => {
      const pct = Math.round((r.bandwidth / max) * 100);
      const vcodec = r.codecs.split(",")[0];
      return `<div class="rung">
        <span class="rung-label">${esc(r.preset)}</span>
        <span class="rung-res">${esc(r.resolution.replace("x", "×"))}</span>
        <div class="rung-bar"><div class="rung-fill" style="width:${pct}%"></div></div>
        <span class="rung-rate">${humanBitrate(r.bandwidth)}</span>
        <span class="rung-codec">${esc(vcodec)}</span>
      </div>`;
    })
    .join("");
}

function embedSnippet(playlistUrl: string): string {
  return `<video id="v" controls style="width:100%"></video>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1"></script>
<script>var h=new Hls();h.loadSource(${JSON.stringify(playlistUrl)});h.attachMedia(document.getElementById("v"));</script>`;
}

function cardFailed(error?: JobError): string {
  const code = error?.code ?? "FAILED";
  const stage = error?.stage ? ` · ${esc(error.stage)}` : "";
  const retry = error?.retryable
    ? " This may be transient — try uploading again."
    : "";
  return `
    <div class="inspect-card inspect-card--terminal">
      <h1 class="inspect-title">Couldn’t process this video</h1>
      <p class="term-code">${esc(code)}${stage}</p>
      <p class="term-msg">${esc(error?.message ?? "The file couldn’t be inspected.")}${retry}</p>
      <a href="/" class="btn btn-primary">Upload another file</a>
    </div>
  `;
}

function cardMessage(title: string, msg: string): string {
  return `
    <div class="inspect-card inspect-card--terminal">
      <h1 class="inspect-title">${esc(title)}</h1>
      <p class="term-msg">${esc(msg)}</p>
      <a href="/" class="btn btn-primary">Back to upload</a>
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
    render();
  });
  document.getElementById("cancel-keep-btn")?.addEventListener("click", () => {
    confirmingCancel = false;
    render();
  });
  document
    .getElementById("cancel-confirm-btn")
    ?.addEventListener("click", () => void doCancel());

  if (view.tag === "done") {
    const base = apiBase();
    document
      .getElementById("copy-master")
      ?.addEventListener("click", (e) =>
        copyText(
          base + (view as Extract<View, { tag: "done" }>).results.playlist,
          e.currentTarget as HTMLElement,
        ),
      );
    document
      .getElementById("copy-embed")
      ?.addEventListener("click", (e) =>
        copyText(
          embedSnippet(
            base + (view as Extract<View, { tag: "done" }>).results.playlist,
          ),
          e.currentTarget as HTMLElement,
        ),
      );
  }
}

function mountDonePlayer(results: JobResults): void {
  const mount = document.getElementById("player-mount");
  if (!mount || !jobId) return;
  document
    .getElementById("watch-scroll")
    ?.addEventListener("click", () =>
      document
        .getElementById("watch-detail")
        ?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  const base = apiBase();
  const id = jobId;
  const myGen = gen;
  // fetch the sprite storyboard first so the player mounts with seek-scrub wired; then the ladder.
  void loadStoryboard(id).then((sb) => {
    if (myGen !== gen) return;
    mount.classList.add("player--stage");
    player = mountPlayer(mount, {
      playlist: base + results.playlist,
      poster: base + results.poster,
      storyboard: sb,
      spriteUrl: spriteUrl(id),
    });
  });
  void getManifest(id)
    .then((m) => {
      if (myGen !== gen) return;
      const rows = document.getElementById("ladder-rows");
      if (rows) rows.innerHTML = renderLadder(m);
    })
    .catch(() => {
      const ladder = document.getElementById("ladder");
      if (ladder) ladder.remove(); // pre-manifest job — drop the panel rather than show a stub
    });
}

async function copyText(text: string, btn: HTMLElement): Promise<void> {
  const prev = btn.textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied!";
  } catch {
    btn.textContent = "Copy failed — press ⌘/Ctrl-C"; // clipboard blocked (insecure ctx / denied)
  }
  setTimeout(() => {
    btn.textContent = prev;
  }, 1600);
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
  confirmingCancel = false;
  pollTimer = null;
  subsTimer = null;
  unwatch = null;
  player = null;
  errorAttempts = 0;
  // gen is NOT reset — it stays monotonic across mounts so a stale in-flight load() can't write into a remount's DOM

  void load();

  return () => {
    cancelPoll();
    if (player) {
      player.destroy();
      player = null;
    }
  };
}
