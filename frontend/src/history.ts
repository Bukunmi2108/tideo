import { listJobs, apiBase, ApiError, type JobSummary } from "./api"
import { esc, humanDuration, relativeTime, expiresIn, siteHeader } from "./render"

const PAGE = 24

const BADGES: Record<string, string> = {
  done: "done", failed: "failed", cancelled: "cancelled", expired: "expired",
}

function posterCell(j: JobSummary): string {
  if (j.poster) return `<img class="hist-poster" src="${esc(apiBase() + j.poster)}" alt="" loading="lazy">`
  // designed placeholder for jobs whose poster is gone (failed/cancelled/expired) or not yet made
  return `<div class="hist-poster hist-poster--empty" aria-hidden="true">
    <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5">
      <rect x="3" y="3" width="18" height="18" rx="2"/><path d="m3 16 5-5 4 4 3-3 6 6"/>
    </svg></div>`
}

function card(j: JobSummary): string {
  const status = j.status
  const badge = `<span class="badge hist-badge hist-badge--${status}">${BADGES[status] ?? esc(status)}</span>`
  const dur = j.duration != null ? humanDuration(j.duration) : ""
  const when = relativeTime(j.created_at)
  const exp = status === "done" ? expiresIn(j.expires_at) : ""
  const sub = [dur, when].filter(Boolean).join(" · ")
  return `<a class="hist-card" href="/job?id=${encodeURIComponent(j.job_id)}">
    ${posterCell(j)}
    <div class="hist-meta">
      <div class="hist-top"><span class="hist-name">${esc(j.source_filename ?? j.job_id)}</span>${badge}</div>
      <div class="hist-sub">${esc(sub)}</div>
      ${exp ? `<div class="hist-exp">${esc(exp)}</div>` : ""}
    </div>
  </a>`
}

function skeletonGrid(): string {
  const cell = `<div class="hist-card hist-card--sk">
    <div class="sk hist-poster"></div>
    <div class="hist-meta"><div class="sk sk-title"></div><div class="sk sk-val"></div></div>
  </div>`
  return `<div class="hist-grid">${cell.repeat(8)}</div>`
}

function emptyState(): string {
  return `<div class="hist-empty">
    <p class="hist-empty-head">Nothing here yet</p>
    <p class="hist-empty-sub">Your transcoded videos will show up here.</p>
    <a href="/" class="btn btn-primary">Upload your first video</a>
  </div>`
}

export function mount(root: HTMLElement): () => void {
  let cancelled = false
  const ctrl = new AbortController()
  let offset = 0

  root.innerHTML = `${siteHeader()}
    <main class="hist-main">
      <h1 class="hist-title">History</h1>
      <div class="hist-body">${skeletonGrid()}</div>
    </main>`
  const body = root.querySelector(".hist-body") as HTMLElement

  async function loadPage(append: boolean): Promise<void> {
    try {
      const page = await listJobs({ limit: PAGE, offset }, ctrl.signal)
      if (cancelled) return
      offset += page.items.length

      if (!append && page.items.length === 0) {
        body.innerHTML = emptyState()
        return
      }
      const cards = page.items.map(card).join("")
      if (append) {
        document.querySelector(".hist-grid")?.insertAdjacentHTML("beforeend", cards)
        document.querySelector(".hist-more")?.remove()
      } else {
        body.innerHTML = `<div class="hist-grid">${cards}</div>`
      }
      if (page.has_more) {
        body.insertAdjacentHTML("beforeend", `<button class="btn btn-ghost hist-more">Load more</button>`)
        body.querySelector(".hist-more")?.addEventListener("click", () => loadPage(true), { once: true })
      }
    } catch (e) {
      if (cancelled || (e instanceof DOMException && e.name === "AbortError")) return
      const msg = e instanceof ApiError && e.retryable ? "Service busy — try again shortly." : "Couldn't load history."
      body.innerHTML = `<div class="hist-empty"><p class="hist-empty-sub">${esc(msg)}</p></div>`
    }
  }

  loadPage(false)
  return () => { cancelled = true; ctrl.abort() }
}
