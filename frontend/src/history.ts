import { listJobs, apiBase, ApiError, type JobSummary } from "./api";
import {
  esc,
  humanDuration,
  relativeTime,
  expiresIn,
  siteHeader,
  siteFooter,
} from "./render";
import {
  applySprite,
  loadStoryboard,
  playLoop,
  spriteUrl,
  type Loop,
} from "./sprite";

const PAGE = 24;

const BADGES: Record<string, string> = {
  done: "done",
  failed: "failed",
  cancelled: "cancelled",
  expired: "expired",
};

function posterCell(j: JobSummary): string {
  if (j.poster)
    return `<img class="hist-poster" src="${esc(apiBase() + j.poster)}" alt="" loading="lazy">`;
  // designed placeholder for jobs whose poster is gone (failed/cancelled/expired) or not yet made
  return `<div class="hist-poster hist-poster--empty" aria-hidden="true">
    <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5">
      <rect x="3" y="3" width="18" height="18" rx="2"/><path d="m3 16 5-5 4 4 3-3 6 6"/>
    </svg></div>`;
}

function card(j: JobSummary, i = 0): string {
  const status = j.status;
  const badge = `<span class="badge hist-badge hist-badge--${status}">${BADGES[status] ?? esc(status)}</span>`;
  const dur = j.duration != null ? humanDuration(j.duration) : "";
  const when = relativeTime(j.created_at);
  const exp = status === "done" ? expiresIn(j.expires_at) : "";
  const sub = [dur, when].filter(Boolean).join(" · ");
  const playable = status === "done" && !!j.poster;
  const play = playable
    ? `<div class="hist-scrub" aria-hidden="true"></div><div class="hist-play" aria-hidden="true"><span>▶</span></div>`
    : "";
  const data = playable ? ` data-job="${esc(j.job_id)}"` : "";
  return `<a class="hist-card"${data} style="--i:${i % 24}" href="/job?id=${encodeURIComponent(j.job_id)}">
    ${posterCell(j)}
    ${play}
    <div class="hist-meta">
      <div class="hist-top"><span class="hist-name">${esc(j.source_filename ?? j.job_id)}</span>${badge}</div>
      <div class="hist-sub">${esc(sub)}</div>
      ${exp ? `<div class="hist-exp">${esc(exp)}</div>` : ""}
    </div>
  </a>`;
}

function hero(j: JobSummary): string {
  const dur =
    j.duration != null ? `<b>${esc(humanDuration(j.duration))}</b>` : "";
  const when = relativeTime(j.created_at);
  const spec = [dur, when ? `transcoded ${esc(when)}` : "", "adaptive HLS"]
    .filter(Boolean)
    .join(" · ");
  const href = `/job?id=${encodeURIComponent(j.job_id)}`;
  return `<section class="hero">
    <img class="hero-bg" src="${esc(apiBase() + j.poster!)}" alt="" />
    <div class="hero-inner">
      <p class="hero-eyebrow">Latest transcode</p>
      <h2 class="hero-name">${esc(j.source_filename ?? j.job_id)}</h2>
      <p class="hero-spec">${spec}</p>
      <div class="hero-actions">
        <a class="btn btn-primary btn-lg" href="${href}">▶ Play</a>
        <a class="btn btn-ghost btn-lg" href="${href}">Details</a>
      </div>
    </div>
  </section>`;
}

function featured(items: JobSummary[]): JobSummary | null {
  return items.find((j) => j.status === "done" && !!j.poster) ?? null;
}

function skeletonGrid(): string {
  const cell = `<div class="hist-card hist-card--sk">
    <div class="sk hist-poster"></div>
    <div class="hist-meta"><div class="sk sk-title"></div><div class="sk sk-val"></div></div>
  </div>`;
  return `<div class="hist-grid">${cell.repeat(8)}</div>`;
}

function emptyState(): string {
  return `<div class="hist-empty">
    <p class="hist-empty-head">Nothing here yet</p>
    <p class="hist-empty-sub">Your transcoded videos will show up here.</p>
    <a href="/upload" class="btn btn-primary">Upload your first video</a>
  </div>`;
}

export function mount(root: HTMLElement): () => void {
  let cancelled = false;
  const ctrl = new AbortController();
  let offset = 0;

  root.innerHTML = `${siteHeader()}
    <main class="hist-main">
      <div class="hist-body">${skeletonGrid()}</div>
    </main>
    ${siteFooter()}`;
  const body = root.querySelector(".hist-body") as HTMLElement;

  // Hover-scrub: each playable card flips through its sprite while pointed at — Tideo previewing itself.
  const loops = new Map<string, Loop>();
  async function startScrub(card: HTMLElement): Promise<void> {
    const jid = card.dataset.job;
    if (!jid || loops.has(jid)) return;
    const scrub = card.querySelector<HTMLElement>(".hist-scrub");
    if (!scrub) return;
    const sb = await loadStoryboard(jid);
    if (cancelled || !sb || !card.matches(":hover") || loops.has(jid)) return;
    applySprite(scrub, sb, spriteUrl(jid));
    scrub.classList.add("on");
    loops.set(jid, playLoop(scrub, sb, 6));
  }
  function stopScrub(card: HTMLElement): void {
    const jid = card.dataset.job;
    if (!jid) return;
    loops.get(jid)?.stop();
    loops.delete(jid);
    card.querySelector(".hist-scrub")?.classList.remove("on");
  }
  const onOver = (e: PointerEvent) => {
    const card = (e.target as HTMLElement).closest<HTMLElement>(
      ".hist-card[data-job]",
    );
    if (card) void startScrub(card);
  };
  const onOut = (e: PointerEvent) => {
    const card = (e.target as HTMLElement).closest<HTMLElement>(
      ".hist-card[data-job]",
    );
    if (card && !card.contains(e.relatedTarget as Node)) stopScrub(card);
  };
  body.addEventListener("pointerover", onOver);
  body.addEventListener("pointerout", onOut);

  async function loadPage(append: boolean): Promise<void> {
    try {
      const page = await listJobs({ limit: PAGE, offset }, ctrl.signal);
      if (cancelled) return;
      offset += page.items.length;

      if (!append && page.items.length === 0) {
        body.innerHTML = emptyState();
        return;
      }
      const cards = page.items.map(card).join("");
      if (append) {
        document
          .querySelector(".hist-grid")
          ?.insertAdjacentHTML("beforeend", cards);
        document.querySelector(".hist-more")?.remove();
      } else {
        const feat = featured(page.items);
        body.innerHTML = `${feat ? hero(feat) : ""}
          <h2 class="hist-title">Library</h2>
          <div class="hist-grid">${cards}</div>`;
      }
      if (page.has_more) {
        body.insertAdjacentHTML(
          "beforeend",
          `<button class="btn btn-ghost hist-more">Load more</button>`,
        );
        body
          .querySelector(".hist-more")
          ?.addEventListener("click", () => loadPage(true), { once: true });
      }
    } catch (e) {
      if (cancelled || (e instanceof DOMException && e.name === "AbortError"))
        return;
      const msg =
        e instanceof ApiError && e.retryable
          ? "Service busy — try again shortly."
          : "The API may be waking up. Refresh shortly if this takes more than a minute.";
      body.innerHTML = `<div class="hist-empty"><p class="hist-empty-sub">${esc(msg)}</p></div>`;
    }
  }

  loadPage(false);
  return () => {
    cancelled = true;
    ctrl.abort();
    loops.forEach((l) => l.stop());
    loops.clear();
    body.removeEventListener("pointerover", onOver);
    body.removeEventListener("pointerout", onOut);
  };
}
