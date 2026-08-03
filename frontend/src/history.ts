import { ApiError, apiBase, listJobs, type JobSummary } from "./api";
import { icon } from "./icons";
import {
  esc,
  expiresIn,
  humanDuration,
  relativeTime,
  siteFooter,
  siteHeader,
} from "./render";
import {
  applySprite,
  loadStoryboard,
  playLoop,
  spriteUrl,
  type Loop,
} from "./sprite";

const PAGE = 24;
const REFRESH_INTERVAL = 5000;
const PROCESSING = new Set([
  "inspecting",
  "awaiting_choice",
  "queued",
  "transcoding",
]);

type Filter = "all" | "processing" | "ready" | "failed";

const FILTERS: Array<{ value: Filter; label: string }> = [
  { value: "all", label: "All" },
  { value: "processing", label: "Processing" },
  { value: "ready", label: "Ready" },
  { value: "failed", label: "Failed" },
];

const STATUS_LABELS: Record<string, string> = {
  inspecting: "Inspecting",
  awaiting_choice: "Needs choices",
  queued: "Queued",
  transcoding: "Processing",
  done: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
  expired: "Expired",
};

function posterCell(job: JobSummary): string {
  if (job.poster)
    return `<img class="hist-poster" src="${esc(apiBase() + job.poster)}" alt="" loading="lazy" decoding="async">`;
  return `<div class="hist-poster hist-poster--empty" aria-hidden="true">${icon("video")}</div>`;
}

function progressPercent(job: JobSummary): number {
  const values =
    job.presets?.map((preset) => job.progress?.[preset] ?? 0) ??
    Object.values(job.progress ?? {});
  if (values.length === 0) return 0;
  return Math.round(
    values.reduce((total, value) => total + Math.max(0, Math.min(100, value)), 0) /
      values.length,
  );
}

function card(job: JobSummary, index: number): string {
  const label = STATUS_LABELS[job.status] ?? job.status;
  const filename = job.source_filename ?? job.job_id;
  const duration = job.duration != null ? humanDuration(job.duration) : "";
  const created = relativeTime(job.created_at);
  const expiry = job.status === "done" ? expiresIn(job.expires_at) : "";
  const sub = [duration, created].filter(Boolean).join(" · ");
  const playable = job.status === "done" && Boolean(job.poster);
  const active = PROCESSING.has(job.status);
  const progress = progressPercent(job);
  const preview = playable
    ? `<div class="hist-scrub" aria-hidden="true"></div><div class="hist-play" aria-hidden="true"><span>${icon("play")}</span></div>`
    : "";
  const data = playable ? ` data-job="${esc(job.job_id)}"` : "";
  const progressMarkup = active
    ? `<div class="hist-progress">
        <div class="hist-progress-copy"><span>${progress > 0 ? `${progress}% complete` : "Waiting to start"}</span></div>
        <div class="hist-progress-track" role="progressbar" aria-label="${esc(filename)} processing progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span style="transform:scaleX(${progress / 100})"></span></div>
      </div>`
    : "";
  return `<a class="hist-card"${data} style="--i:${index % PAGE}" href="/job?id=${encodeURIComponent(job.job_id)}" aria-label="${esc(filename)}, ${esc(label)}">
    <div class="hist-media">${posterCell(job)}${preview}</div>
    <div class="hist-meta">
      <div class="hist-top"><span class="hist-name" title="${esc(filename)}">${esc(filename)}</span><span class="status-badge hist-badge hist-badge--${esc(job.status)}">${esc(label)}</span></div>
      ${sub ? `<div class="hist-sub">${esc(sub)}</div>` : ""}
      ${expiry ? `<div class="hist-exp">${esc(expiry)}</div>` : ""}
      ${progressMarkup}
    </div>
  </a>`;
}

function skeletonGrid(): string {
  const skeleton = `<div class="hist-card hist-card--sk" aria-hidden="true">
    <div class="skeleton hist-poster"></div>
    <div class="hist-meta"><div class="skeleton sk-title"></div><div class="skeleton sk-val"></div></div>
  </div>`;
  return `<div class="hist-grid hist-grid--loading" aria-busy="true" aria-label="Loading videos">${skeleton.repeat(8)}</div>`;
}

function emptyState(filter: Filter): string {
  const filtered = filter !== "all";
  return `<div class="empty-state hist-empty">
    <p class="empty-state-title">${filtered ? `No ${FILTERS.find((item) => item.value === filter)?.label.toLowerCase()} videos` : "No videos yet"}</p>
    <p class="empty-state-copy">${filtered ? "Try another status or return to all videos." : "Upload a source video and its processing status will appear here."}</p>
    ${filtered ? `<button class="btn btn-ghost hist-show-all" type="button">Show all videos</button>` : `<a href="/upload" class="btn btn-primary">Upload your first video</a>`}
  </div>`;
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError && error.retryable
    ? "Tideo is busy and couldn’t load your videos."
    : "Tideo couldn’t reach your videos right now.";
}

export function mount(root: HTMLElement): () => void {
  const lifetime = new AbortController();
  let request: AbortController | null = null;
  let refreshRequest: AbortController | null = null;
  let refreshTimer: number | null = null;
  let filter: Filter = "all";
  let items: JobSummary[] = [];
  let offset = 0;
  let hasMore = false;
  let loading = false;
  let paginationError: string | null = null;
  let cancelled = false;

  root.innerHTML = `${siteHeader()}
    <main id="main-content" class="hist-main">
      <div class="hist-shell">
        <header class="hist-toolbar">
          <div class="hist-heading">
            <p class="hist-eyebrow">Guest workspace</p>
            <h1>My videos</h1>
            <p>Jobs in this browser session appear here. Outputs are temporary and shared links remain public until expiry.</p>
          </div>
          <a class="btn btn-primary" href="/upload">${icon("upload")} Upload video</a>
        </header>
        <div class="hist-filters" role="group" aria-label="Filter videos by status">
          ${FILTERS.map((item) => `<button class="hist-filter" type="button" data-filter="${item.value}" aria-pressed="${item.value === filter}">${item.label}</button>`).join("")}
        </div>
        <div class="hist-body">${skeletonGrid()}</div>
      </div>
    </main>
    ${siteFooter()}`;

  const body = root.querySelector<HTMLElement>(".hist-body")!;
  const filterBar = root.querySelector<HTMLElement>(".hist-filters")!;
  const loops = new Map<string, Loop>();
  const storyboards = new Map<
    string,
    ReturnType<typeof loadStoryboard>
  >();
  const reducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function stopLoops(): void {
    loops.forEach((loop) => loop.stop());
    loops.clear();
  }

  async function startScrub(element: HTMLElement): Promise<void> {
    if (reducedMotion) return;
    const jobId = element.dataset.job;
    if (!jobId || loops.has(jobId)) return;
    const scrub = element.querySelector<HTMLElement>(".hist-scrub");
    if (!scrub) return;
    let storyboard = storyboards.get(jobId);
    if (!storyboard) {
      storyboard = loadStoryboard(jobId, lifetime.signal);
      storyboards.set(jobId, storyboard);
    }
    const value = await storyboard;
    if (
      cancelled ||
      !value ||
      !element.matches(":hover") ||
      loops.has(jobId)
    )
      return;
    applySprite(scrub, value, spriteUrl(jobId));
    scrub.classList.add("on");
    loops.set(jobId, playLoop(scrub, value, 6));
  }

  function stopScrub(element: HTMLElement): void {
    const jobId = element.dataset.job;
    if (!jobId) return;
    loops.get(jobId)?.stop();
    loops.delete(jobId);
    element.querySelector(".hist-scrub")?.classList.remove("on");
  }

  function updateFilters(): void {
    filterBar.querySelectorAll<HTMLButtonElement>(".hist-filter").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
    });
  }

  function renderItems(): void {
    stopLoops();
    if (items.length === 0) {
      body.innerHTML = emptyState(filter);
      return;
    }
    const pagination = paginationError
      ? `<div class="hist-page-error" role="alert"><p>${esc(paginationError)} Your existing videos are still available.</p><button class="btn btn-ghost hist-page-retry" type="button">${icon("retry")} Try again</button></div>`
      : hasMore
        ? `<button class="btn btn-ghost hist-more" type="button">Load more</button>`
        : "";
    body.innerHTML = `<div class="hist-grid">${items.map(card).join("")}</div><div class="hist-pagination">${pagination}</div>`;
  }

  function clearRefresh(): void {
    if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    refreshTimer = null;
    refreshRequest?.abort();
    refreshRequest = null;
  }

  function scheduleRefresh(): void {
    clearRefresh();
    if (!items.some((item) => PROCESSING.has(item.status))) return;
    refreshTimer = window.setTimeout(() => void refreshActive(), REFRESH_INTERVAL);
  }

  function listOptions(pageOffset: number, limit = PAGE) {
    return {
      limit,
      offset: pageOffset,
      ...(filter === "all" ? {} : { status: filter }),
    };
  }

  async function refreshActive(): Promise<void> {
    if (cancelled || loading) return scheduleRefresh();
    const currentRequest = new AbortController();
    refreshRequest = currentRequest;
    try {
      const visible = Math.min(50, Math.max(PAGE, offset));
      const page = await listJobs(
        listOptions(0, visible),
        currentRequest.signal,
      );
      if (cancelled) return;
      if (filter === "processing") {
        items = page.items;
        offset = page.items.length;
        hasMore = page.has_more;
      } else {
        const freshIds = new Set(page.items.map((item) => item.job_id));
        items = [
          ...page.items,
          ...items.filter((item) => !freshIds.has(item.job_id)),
        ];
        hasMore = hasMore || page.has_more;
      }
      renderItems();
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        // A background refresh is best effort; keep the current cards untouched.
      }
    } finally {
      if (refreshRequest === currentRequest) {
        refreshRequest = null;
        if (!cancelled) scheduleRefresh();
      }
    }
  }

  async function loadPage(append: boolean): Promise<void> {
    if (loading) return;
    loading = true;
    paginationError = null;
    const more = body.querySelector<HTMLButtonElement>(".hist-more");
    if (more) {
      more.disabled = true;
      more.textContent = "Loading…";
    }
    request?.abort();
    const currentRequest = new AbortController();
    request = currentRequest;
    try {
      const page = await listJobs(
        listOptions(append ? offset : 0),
        currentRequest.signal,
      );
      if (cancelled) return;
      if (append) {
        const known = new Set(items.map((item) => item.job_id));
        items = [...items, ...page.items.filter((item) => !known.has(item.job_id))];
      } else {
        items = page.items;
        offset = 0;
      }
      offset += page.items.length;
      hasMore = page.has_more;
      renderItems();
      scheduleRefresh();
    } catch (error) {
      if (cancelled || (error instanceof DOMException && error.name === "AbortError"))
        return;
      if (append) {
        paginationError = errorMessage(error);
        renderItems();
      } else {
        body.innerHTML = `<div class="empty-state hist-empty hist-load-error" role="alert">
          <p class="empty-state-title">Couldn’t load your videos</p>
          <p class="empty-state-copy">${esc(errorMessage(error))} Your session has not been changed.</p>
          <button class="btn btn-primary hist-retry" type="button">${icon("retry")} Try again</button>
        </div>`;
      }
    } finally {
      if (request === currentRequest) {
        loading = false;
        request = null;
      }
    }
  }

  function selectFilter(next: Filter): void {
    if (next === filter && items.length > 0) return;
    clearRefresh();
    request?.abort();
    filter = next;
    items = [];
    offset = 0;
    hasMore = false;
    paginationError = null;
    updateFilters();
    body.innerHTML = skeletonGrid();
    loading = false;
    void loadPage(false);
  }

  filterBar.addEventListener(
    "click",
    (event) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>(
        ".hist-filter[data-filter]",
      );
      if (button) selectFilter(button.dataset.filter as Filter);
    },
    { signal: lifetime.signal },
  );
  body.addEventListener(
    "click",
    (event) => {
      const target = event.target as HTMLElement;
      if (target.closest(".hist-more, .hist-page-retry")) void loadPage(true);
      if (target.closest(".hist-retry")) {
        body.innerHTML = skeletonGrid();
        void loadPage(false);
      }
      if (target.closest(".hist-show-all")) selectFilter("all");
    },
    { signal: lifetime.signal },
  );
  body.addEventListener(
    "pointerover",
    (event) => {
      const element = (event.target as HTMLElement).closest<HTMLElement>(
        ".hist-card[data-job]",
      );
      if (element) void startScrub(element);
    },
    { signal: lifetime.signal },
  );
  body.addEventListener(
    "pointerout",
    (event) => {
      const element = (event.target as HTMLElement).closest<HTMLElement>(
        ".hist-card[data-job]",
      );
      if (element && !element.contains(event.relatedTarget as Node))
        stopScrub(element);
    },
    { signal: lifetime.signal },
  );

  void loadPage(false);
  return () => {
    cancelled = true;
    request?.abort();
    clearRefresh();
    lifetime.abort();
    stopLoops();
    storyboards.clear();
  };
}
