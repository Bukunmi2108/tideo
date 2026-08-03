import { apiBase, type UploadResponse } from "./api";
import { icon } from "./icons";
import { esc, humanBytes, siteFooter, siteHeader } from "./render";
import { navigate } from "./router";
import { SESSION_HEADER, guestSession } from "./session";
import { waitForBackendReady } from "./wake";

type UploadingState = {
  tag: "uploading";
  file: File;
  loaded: number;
  total: number;
  rate: number;
};

type WaitingState = {
  tag: "waiting";
  phase: "backend" | "retry";
  file: File;
  attempt: number;
  delayMs?: number;
};

type Failure = {
  code: string;
  headline: string;
  message: string;
  retryable: boolean;
};

type State =
  | { tag: "idle" }
  | UploadingState
  | WaitingState
  | { tag: "dedup"; jobId: string; filename: string }
  | ({ tag: "rejected"; file?: File } & Failure);

const ALLOWED_EXTS = [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"];
const MAX_BYTES = 4 * 1024 ** 3;
const MAX_UPLOAD_RETRIES = 3;

function validationFailure(files: File[]): Failure | null {
  if (files.length > 1) {
    return {
      code: "MULTIPLE_FILES",
      headline: "Choose one video",
      message: "Choose one video at a time. Remove the extra files and try again.",
      retryable: false,
    };
  }

  const file = files[0];
  if (!file) return null;
  if (file.size === 0) {
    return {
      code: "EMPTY_FILE",
      headline: "This file is empty",
      message: "The selected file is empty. Choose a video file with content and try again.",
      retryable: false,
    };
  }

  const ext = `.${file.name.split(".").pop() ?? ""}`.toLowerCase();
  if (!ALLOWED_EXTS.includes(ext)) {
    const label = ext === "." ? "Files without an extension" : `${ext.slice(1).toUpperCase()} files`;
    return {
      code: "UNSUPPORTED_MEDIA",
      headline: "Unsupported format",
      message: `${label} are not supported. Choose MP4, MOV, MKV, WebM, AVI, or M4V.`,
      retryable: false,
    };
  }

  if (file.size > MAX_BYTES) {
    return {
      code: "UPLOAD_TOO_LARGE",
      headline: "File too large",
      message: `${file.name} is ${humanBytes(file.size)}. The limit is 4 GB. Choose a smaller file.`,
      retryable: false,
    };
  }

  return null;
}

function offlineFailure(): Failure {
  return {
    code: "OFFLINE",
    headline: "You are offline",
    message: "You appear to be offline. Reconnect, then retry this upload.",
    retryable: true,
  };
}

function serverFailure(status: number, code: string, retryable: boolean): Failure {
  if (status === 413 || code === "UPLOAD_TOO_LARGE") {
    return {
      code: "UPLOAD_TOO_LARGE",
      headline: "File too large",
      message: "The service rejected this file because it exceeds the 4 GB limit. Choose a smaller file.",
      retryable: false,
    };
  }
  if (status === 415 || code === "UNSUPPORTED_MEDIA") {
    return {
      code: "UNSUPPORTED_MEDIA",
      headline: "Unsupported video",
      message: "Tideo could not read this file as a supported video. Choose another file or convert it to MP4 first.",
      retryable: false,
    };
  }
  if (code === "INVALID_UPLOAD") {
    return {
      code,
      headline: "Upload rejected",
      message: "The service could not accept this file. Check that it contains video and choose it again.",
      retryable: false,
    };
  }
  if (code === "STORAGE_PRESSURE") {
    return {
      code,
      headline: "Storage is temporarily full",
      message: "Temporary storage is full. Keep this tab open and retry shortly.",
      retryable: true,
    };
  }
  if (code === "INSPECTION_UNAVAILABLE") {
    return {
      code,
      headline: "Inspection could not start",
      message: "Tideo received the file but could not start inspection. Retry the upload shortly.",
      retryable: true,
    };
  }
  if (status === 401) {
    return {
      code: code || "SESSION_REQUIRED",
      headline: "Session could not be verified",
      message: "Refresh this page, then choose the file again.",
      retryable: false,
    };
  }

  return {
    code: code || "SERVER_ERROR",
    headline: "Upload could not finish",
    message: "The upload service could not finish this request. Retry this file in a moment.",
    retryable: retryable || status >= 500,
  };
}

function isUploadResponse(value: unknown): value is UploadResponse {
  if (!value || typeof value !== "object") return false;
  const response = value as Partial<UploadResponse>;
  return (
    typeof response.job_id === "string" &&
    response.job_id.length > 0 &&
    (response.dedupe === "hit" || response.dedupe === "miss")
  );
}

export function mount(root: HTMLElement): () => void {
  let state: State = { tag: "idle" };
  let dragCount = 0;
  let currentXhr: XMLHttpRequest | null = null;
  let currentWake: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let operationSequence = 0;
  const mountController = new AbortController();
  const { signal } = mountController;

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = ALLOWED_EXTS.join(",");
  fileInput.hidden = true;
  fileInput.tabIndex = -1;
  document.body.appendChild(fileInput);

  function setState(next: State, focusId?: string): void {
    state = next;
    render();
    if (!focusId) return;
    const target = root.querySelector<HTMLElement>(`#${focusId}`);
    if (target && !target.matches("button, a, input")) target.tabIndex = -1;
    target?.focus();
  }

  function render(): void {
    root.innerHTML = `${siteHeader()}
      <main id="main-content" class="upload-main">
        <div class="upload-shell">
          <header class="upload-intro">
            <p class="upload-eyebrow">Temporary video processing</p>
            <h1>Upload video</h1>
            <p>Choose one source file. Tideo will inspect it before you select outputs or start transcoding.</p>
          </header>
          <aside class="upload-trust" role="note">
            <strong>Before you upload</strong>
            <p>Files and outputs expire automatically. My videos stays with this browser session. Anyone with a shared link can watch, so keep sensitive material off this public demonstration.</p>
            <a href="/privacy">Privacy details</a>
          </aside>
          <div class="upload-stage">${card()}</div>
        </div>
      </main>
      ${siteFooter()}`;
    bind();
  }

  function card(): string {
    switch (state.tag) {
      case "idle":
        return cardIdle();
      case "uploading":
        return cardUploading(state);
      case "waiting":
        return cardWaiting(state);
      case "dedup":
        return cardDedup(state);
      case "rejected":
        return cardRejected(state);
    }
  }

  function cardIdle(): string {
    return `<button class="upload-card upload-zone" id="drop-zone" type="button">
      <span class="upload-icon">${icon("upload")}</span>
      <span class="upload-zone-title">Drop or choose a video</span>
      <span class="upload-zone-copy">Browse, drop, or paste a video file from your clipboard.</span>
      <span class="upload-hint">MP4, MOV, MKV, WEBM, AVI, M4V · One file · Up to 4 GB</span>
    </button>`;
  }

  function cardUploading(upload: UploadingState): string {
    const pct = upload.total > 0 ? Math.round((upload.loaded / upload.total) * 100) : 0;
    const rate = upload.rate > 0 ? ` · ${humanBytes(upload.rate)}/s` : "";
    const valueText = `${humanBytes(upload.loaded)} of ${humanBytes(upload.total)}`;
    return `<section class="upload-card upload-state" aria-labelledby="upload-state-title">
      <span class="upload-state-label">Uploading</span>
      <h2 id="upload-state-title">Uploading video</h2>
      <p class="upload-filename" title="${esc(upload.file.name)}">${esc(upload.file.name)}</p>
      <div class="progress-bar-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-valuetext="${valueText}" aria-label="Upload progress">
        <div class="progress-bar-fill" style="transform: scaleX(${pct / 100})"></div>
      </div>
      <p class="upload-stats">${valueText}${rate}</p>
      <p class="sr-only" id="upload-live" aria-live="polite" aria-atomic="true"></p>
      <button class="btn btn-ghost" id="cancel-upload-btn" type="button">Cancel upload</button>
    </section>`;
  }

  function cardWaiting(waiting: WaitingState): string {
    const retrying = waiting.phase === "retry";
    const seconds = Math.max(1, Math.ceil((waiting.delayMs ?? 0) / 1000));
    const title = retrying ? "Retrying upload" : "Starting the temporary service";
    const copy = retrying
      ? `The connection dropped. Tideo will retry automatically in up to ${seconds} seconds. Your file stays in this tab.`
      : "The service sleeps when idle. Your file stays in this tab and the upload will begin automatically when it is ready.";
    const cancelId = retrying ? "cancel-retry-btn" : "cancel-wake-btn";
    return `<section class="upload-card upload-state" aria-labelledby="upload-state-title" aria-live="polite">
      <span class="upload-icon upload-icon--busy">${icon("spinner")}</span>
      <span class="upload-state-label">${retrying ? "Connection recovery" : "Service check"}</span>
      <h2 id="upload-state-title">${title}</h2>
      <p class="upload-filename" title="${esc(waiting.file.name)}">${esc(waiting.file.name)}</p>
      <p class="upload-state-copy">${copy}</p>
      <button class="btn btn-ghost" id="${cancelId}" type="button">Cancel</button>
    </section>`;
  }

  function cardDedup(result: Extract<State, { tag: "dedup" }>): string {
    return `<section class="upload-card upload-state" aria-labelledby="upload-state-title">
      <span class="upload-icon upload-icon--success">${icon("check")}</span>
      <span class="upload-state-label">Already processed</span>
      <h2 id="upload-state-title">Your result is ready</h2>
      <p class="upload-state-copy"><strong>${esc(result.filename)}</strong> was already processed in this browser session.</p>
      <div class="upload-actions">
        <a href="/job?id=${encodeURIComponent(result.jobId)}" class="btn btn-primary" id="view-results-link">View results</a>
        <button class="btn btn-ghost" id="choose-another-btn" type="button">Upload another</button>
      </div>
    </section>`;
  }

  function cardRejected(rejection: Extract<State, { tag: "rejected" }>): string {
    return `<section class="upload-card upload-state upload-state--error" role="alert" aria-labelledby="upload-error-title">
      <span class="upload-icon upload-icon--danger">${icon("error")}</span>
      <span class="upload-state-label">${esc(rejection.code.replaceAll("_", " "))}</span>
      <h2 id="upload-error-title">${esc(rejection.headline)}</h2>
      <p class="upload-state-copy">${esc(rejection.message)}</p>
      <div class="upload-actions">
        ${rejection.retryable && rejection.file ? '<button class="btn btn-primary" id="retry-upload-btn" type="button">Retry upload</button>' : ""}
        <button class="btn ${rejection.retryable ? "btn-ghost" : "btn-primary"}" id="choose-another-btn" type="button">Choose another</button>
      </div>
    </section>`;
  }

  function bind(): void {
    root.querySelector("#drop-zone")?.addEventListener("click", () => fileInput.click());
    root.querySelector("#cancel-upload-btn")?.addEventListener("click", cancelToIdle);
    root.querySelector("#cancel-wake-btn")?.addEventListener("click", cancelToIdle);
    root.querySelector("#cancel-retry-btn")?.addEventListener("click", cancelToIdle);
    root.querySelector("#choose-another-btn")?.addEventListener("click", () => {
      stopCurrentOperation();
      setState({ tag: "idle" }, "drop-zone");
    });
    root.querySelector("#retry-upload-btn")?.addEventListener("click", () => {
      if (state.tag !== "rejected" || !state.file) return;
      const file = state.file;
      stopCurrentOperation();
      if (navigator.onLine === false) {
        reject(offlineFailure(), file);
        return;
      }
      void wakeAndUpload(file, operationSequence);
    });
  }

  function stopCurrentOperation(): void {
    operationSequence++;
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    currentWake?.abort();
    currentWake = null;
    currentXhr?.abort();
    currentXhr = null;
  }

  function cancelToIdle(): void {
    stopCurrentOperation();
    setState({ tag: "idle" }, "drop-zone");
  }

  function reject(failure: Failure, file?: File): void {
    const focusId = failure.retryable && file ? "retry-upload-btn" : "choose-another-btn";
    setState({ tag: "rejected", ...failure, file }, focusId);
  }

  function handleFiles(files: File[]): void {
    if (files.length === 0) return;
    stopCurrentOperation();
    const failure = validationFailure(files);
    if (failure) {
      reject(failure);
      return;
    }

    const file = files[0];
    if (navigator.onLine === false) {
      reject(offlineFailure(), file);
      return;
    }
    void wakeAndUpload(file, operationSequence);
  }

  async function wakeAndUpload(file: File, sequence: number): Promise<void> {
    const wake = new AbortController();
    currentWake = wake;
    setState({ tag: "waiting", phase: "backend", file, attempt: 0 });

    const ready = await waitForBackendReady({
      signal: wake.signal,
      onAttempt: (attempt) => {
        if (sequence === operationSequence && !wake.signal.aborted) {
          setState({ tag: "waiting", phase: "backend", file, attempt });
        }
      },
    });
    if (sequence !== operationSequence || wake.signal.aborted) return;
    if (currentWake === wake) currentWake = null;

    if (!ready) {
      reject(
        {
          code: "SERVICE_UNAVAILABLE",
          headline: "Service did not start",
          message: "The temporary service did not become ready. Keep this tab open and retry shortly.",
          retryable: true,
        },
        file,
      );
      return;
    }

    startUpload(file, 0, sequence);
  }

  function startUpload(file: File, attempt: number, sequence: number): void {
    if (sequence !== operationSequence) return;
    setState({ tag: "uploading", file, loaded: 0, total: file.size, rate: 0 });

    let lastBytes = 0;
    let lastTime = Date.now();
    let smoothedRate = 0;
    let lastAnnouncedPercent = -10;
    const xhr = new XMLHttpRequest();
    currentXhr = xhr;
    xhr.open("POST", `${apiBase()}/upload?filename=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader(SESSION_HEADER, guestSession());

    xhr.upload.onprogress = (event) => {
      if (sequence !== operationSequence || currentXhr !== xhr) return;
      const now = Date.now();
      const elapsed = Math.max((now - lastTime) / 1000, 0.001);
      const instantRate = Math.max(0, (event.loaded - lastBytes) / elapsed);
      smoothedRate = smoothedRate === 0 ? instantRate : smoothedRate * 0.7 + instantRate * 0.3;
      lastBytes = event.loaded;
      lastTime = now;
      updateProgress(file, event.loaded, event.total || file.size, smoothedRate);

      const percent = Math.round((event.loaded / (event.total || file.size)) * 100);
      if (percent >= lastAnnouncedPercent + 10 || percent === 100) {
        root.querySelector("#upload-live")!.textContent = `Upload ${percent}% complete.`;
        lastAnnouncedPercent = percent;
      }
    };

    xhr.onload = () => {
      if (sequence !== operationSequence || currentXhr !== xhr) return;
      currentXhr = null;
      if (xhr.status === 202) {
        try {
          const response: unknown = JSON.parse(xhr.responseText);
          if (!isUploadResponse(response)) throw new TypeError("invalid upload response");
          if (response.dedupe === "hit") {
            setState(
              { tag: "dedup", jobId: response.job_id, filename: file.name },
              "view-results-link",
            );
          } else {
            navigate(`/job?id=${response.job_id}`);
          }
          return;
        } catch {
          reject(serverFailure(xhr.status, "INVALID_RESPONSE", true), file);
          return;
        }
      }

      let code = "SERVER_ERROR";
      let retryable = false;
      try {
        const body = JSON.parse(xhr.responseText) as {
          error?: { code?: unknown; retryable?: unknown };
        };
        if (typeof body.error?.code === "string") code = body.error.code;
        retryable = body.error?.retryable === true;
      } catch {
        // The status still selects safe user-facing recovery copy.
      }
      reject(serverFailure(xhr.status, code, retryable), file);
    };

    xhr.onerror = () => {
      if (sequence !== operationSequence || currentXhr !== xhr) return;
      currentXhr = null;
      if (navigator.onLine === false) {
        reject(offlineFailure(), file);
        return;
      }
      if (attempt >= MAX_UPLOAD_RETRIES) {
        reject(
          {
            code: "NETWORK_ERROR",
            headline: "Connection failed",
            message: "Tideo could not reach the upload service. Check your connection, then retry this file.",
            retryable: true,
          },
          file,
        );
        return;
      }

      const delayMs = Math.min(2 ** attempt * 1500, 15_000);
      setState({
        tag: "waiting",
        phase: "retry",
        file,
        attempt: attempt + 1,
        delayMs,
      });
      retryTimer = setTimeout(() => {
        retryTimer = null;
        if (sequence === operationSequence) startUpload(file, attempt + 1, sequence);
      }, delayMs);
    };

    xhr.onabort = () => {
      if (currentXhr === xhr) currentXhr = null;
    };
    xhr.send(file);
  }

  function updateProgress(file: File, loaded: number, total: number, rate: number): void {
    if (state.tag !== "uploading") return;
    state = { tag: "uploading", file, loaded, total, rate };
    const pct = total > 0 ? Math.round((loaded / total) * 100) : 0;
    const valueText = `${humanBytes(loaded)} of ${humanBytes(total)}`;
    const progress = root.querySelector<HTMLElement>("[role=progressbar]");
    progress?.setAttribute("aria-valuenow", String(pct));
    progress?.setAttribute("aria-valuetext", valueText);
    const fill = root.querySelector<HTMLElement>(".progress-bar-fill");
    if (fill) fill.style.transform = `scaleX(${pct / 100})`;
    const stats = root.querySelector<HTMLElement>(".upload-stats");
    if (stats) stats.textContent = `${valueText}${rate > 0 ? ` · ${humanBytes(rate)}/s` : ""}`;
  }

  document.addEventListener(
    "dragenter",
    (event) => {
      event.preventDefault();
      dragCount++;
      if (dragCount === 1) document.body.classList.add("drag-active");
    },
    { signal },
  );
  document.addEventListener(
    "dragleave",
    () => {
      dragCount = Math.max(0, dragCount - 1);
      if (dragCount === 0) document.body.classList.remove("drag-active");
    },
    { signal },
  );
  document.addEventListener("dragover", (event) => event.preventDefault(), { signal });
  document.addEventListener(
    "drop",
    (event) => {
      event.preventDefault();
      dragCount = 0;
      document.body.classList.remove("drag-active");
      handleFiles(Array.from((event as DragEvent).dataTransfer?.files ?? []));
    },
    { signal },
  );
  document.addEventListener(
    "paste",
    (event) => {
      const files = Array.from((event as ClipboardEvent).clipboardData?.files ?? []);
      if (files.length === 0) return;
      event.preventDefault();
      handleFiles(files);
    },
    { signal },
  );
  fileInput.addEventListener(
    "change",
    () => {
      handleFiles(Array.from(fileInput.files ?? []));
      fileInput.value = "";
    },
    { signal },
  );

  render();

  return () => {
    mountController.abort();
    stopCurrentOperation();
    fileInput.remove();
    document.body.classList.remove("drag-active");
  };
}
