import { listJobs, type JobSummary } from "./api";
import { siteHeader } from "./render";
import {
  applySprite,
  loadStoryboard,
  playLoop,
  spriteUrl,
  type Loop,
} from "./sprite";

// The entry screen: what Tideo is, what to expect, and a living filmstrip built from a real job's
// sprite sheet — the product previewing itself.

const STEPS: [string, string, string][] = [
  [
    "Upload",
    "Drop a video",
    "Streamed straight to disk, hashed, and deduped — re-uploading the same file is instant.",
  ],
  [
    "Inspect",
    "We read it",
    "ffprobe pulls codec, resolution, and duration, then recommends a quality ladder that fits.",
  ],
  [
    "Choose",
    "Pick your rungs",
    "Select which resolutions to build — 1080p down to 360p — and turn on captions.",
  ],
  [
    "Encode",
    "Fan out",
    "Every rendition encodes in parallel across workers, then fans back in to one package.",
  ],
  [
    "Stream",
    "Play anywhere",
    "Adaptive HLS that switches quality on the fly, plus a poster, scrub sprite, and an MP4.",
  ],
];

function hero(): string {
  return `<section class="lp-hero">
    <div class="lp-hero-copy">
      <p class="lp-eyebrow">Distributed video transcoding</p>
      <h1 class="lp-title">Upload once.<br />Stream every screen.</h1>
      <p class="lp-lede">Tideo turns one source file into an adaptive HLS ladder — every resolution,
        encoded in parallel, ready to play at whatever bitrate the viewer's connection allows.</p>
      <dl class="lp-ledger">
        <div><dd>1080→360</dd><dt>ladder</dt></div>
        <div><dd>H.264 · AAC</dd><dt>codecs</dt></div>
        <div><dd>HLS + MP4</dd><dt>output</dt></div>
        <div><dd>≤ 4 GB</dd><dt>per file</dt></div>
      </dl>
      <div class="lp-actions">
        <a href="/upload" class="btn btn-primary btn-lg">Upload a video</a>
        <a href="/history" class="btn btn-ghost btn-lg">Browse the library</a>
      </div>
    </div>
    <div class="lp-hero-screen">
      <div class="smpte" aria-hidden="true"></div>
      <div class="film-screen" id="film" aria-hidden="true"></div>
      <div class="film-scrub" aria-hidden="true"><span></span></div>
    </div>
  </section>`;
}

function howItWorks(): string {
  const steps = STEPS.map(
    ([tag, head, body], i) => `
    <li class="lp-step">
      <span class="lp-step-n">${String(i + 1).padStart(2, "0")}</span>
      <div class="lp-step-body">
        <span class="lp-step-tag">${tag}</span>
        <h3 class="lp-step-head">${head}</h3>
        <p class="lp-step-text">${body}</p>
      </div>
    </li>`,
  ).join("");
  return `<section class="lp-how">
    <h2 class="lp-section-title">From file to stream</h2>
    <ol class="lp-steps">${steps}</ol>
  </section>`;
}

export function mount(root: HTMLElement): () => void {
  const ctrl = new AbortController();
  let loop: Loop | null = null;

  root.innerHTML = `${siteHeader()}
    <main class="lp-main">
      ${hero()}
      ${howItWorks()}
    </main>`;

  // hydrate the hero screen with a real job's sprite, auto-advancing like a preview
  const film = root.querySelector<HTMLElement>("#film");
  (async () => {
    try {
      const page = await listJobs({ limit: 12 }, ctrl.signal);
      const feat: JobSummary | undefined = page.items.find(
        (j) => j.status === "done" && !!j.poster,
      );
      if (!feat || !film) return;
      const sb = await loadStoryboard(feat.job_id);
      if (!sb || ctrl.signal.aborted) return;
      applySprite(film, sb, spriteUrl(feat.job_id));
      loop = playLoop(film, sb, 8);
    } catch {
      // no jobs yet or offline — the static screen + SMPTE bars carry the hero on their own
    }
  })();

  return () => {
    ctrl.abort();
    loop?.stop();
  };
}
