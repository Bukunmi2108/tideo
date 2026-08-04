import { siteFooter, siteHeader } from "./render";
import { mountPlayer } from "./player";

const DEMO_STORYBOARD = {
  url: "/demo/sintel-cinematic-storyboard.webp",
  tiles: 8,
  cols: 4,
  rows: 2,
  tile_w: 320,
  tile_h: 180,
  interval: 52.208 / 8,
};

const STEPS: [string, string][] = [
  ["Upload", "Choose one source video up to 5 minutes and 4 GB."],
  ["Inspect", "Review the codec, resolution, duration, and suggested ladder."],
  ["Choose outputs", "Select playback sizes and optional captions before work begins."],
  ["Encode", "Tideo processes each selected rendition in parallel."],
  ["Stream", "Use adaptive HLS, download the MP4, or copy an embed."],
];

function outputProof(): string {
  return `<figure class="lp-proof" id="demo-output">
    <div class="lp-proof-frame">
      <div class="lp-demo-player" id="demo-player" aria-label="Sintel adaptive-stream demo player"></div>
    </div>
    <figcaption class="lp-proof-details">
      <div class="lp-proof-heading">
        <div>
          <p class="lp-proof-kicker">Preprocessed adaptive demo</p>
          <h2>Sintel trailer</h2>
        </div>
        <p class="lp-proof-meta">52 sec · H.264 / AAC · Captions included</p>
      </div>
      <div class="lp-proof-controls">
        <span class="lp-proof-control-label">Try a quality</span>
        <div class="lp-proof-outputs" aria-label="Choose demo playback quality">
          <button type="button" data-demo-quality="480" aria-pressed="false">480p</button>
          <button type="button" data-demo-quality="360" aria-pressed="false">360p</button>
          <button type="button" data-demo-quality="240" aria-pressed="false">240p</button>
        </div>
      </div>
      <p class="lp-proof-credit">
        <a href="https://durian.blender.org/" target="_blank" rel="noreferrer">Sintel</a>
        trailer © Blender Foundation ·
        <a href="https://creativecommons.org/licenses/by/3.0/" target="_blank" rel="license noreferrer">CC BY 3.0</a>
      </p>
    </figcaption>
  </figure>`;
}

function hero(): string {
  return `<section class="lp-hero">
    <div class="lp-hero-copy">
      <p class="lp-eyebrow">Adaptive video transcoding</p>
      <h1 class="lp-title">One upload. Every playback size.</h1>
      <p class="lp-lede">Create adaptive HLS, a web-ready MP4, captions, and embeds from one source video.</p>
      <div class="lp-actions">
        <a href="/upload" class="btn btn-primary btn-lg">Upload video</a>
        <a href="#demo-output" class="btn btn-ghost btn-lg" id="watch-demo" aria-controls="demo-player">Watch demo</a>
      </div>
      <p class="lp-limit">MP4, MOV, MKV, WebM, AVI, or M4V. Up to 5 minutes and 4 GB.</p>
    </div>
    ${outputProof()}
  </section>`;
}

function trustNotice(): string {
  return `<aside class="lp-trust" aria-labelledby="trust-title">
    <div class="lp-trust-heading">
      <p class="lp-trust-kicker">Before you upload</p>
      <h2 id="trust-title">Temporary processing, clear boundaries</h2>
    </div>
    <ul>
      <li><strong>Temporary</strong><span>Files and outputs expire automatically.</span></li>
      <li><strong>This browser</strong><span>My videos is scoped to this browser session.</span></li>
      <li><strong>Public when shared</strong><span>Anyone with a shared link can watch.</span></li>
      <li><strong>Sensitive material</strong><span>Keep it off this public demonstration.</span></li>
    </ul>
    <a href="/privacy">Read privacy details</a>
  </aside>`;
}

function workflow(): string {
  const steps = STEPS.map(
    ([title, body]) => `<li class="lp-step">
      <h3 class="lp-step-title">${title}</h3>
      <p>${body}</p>
    </li>`,
  ).join("");

  return `<section class="lp-workflow" aria-labelledby="workflow-title">
    <div class="lp-section-heading">
      <p class="lp-section-kicker">The workflow</p>
      <h2 id="workflow-title">You decide before Tideo starts expensive work.</h2>
      <p>Inspect the source and choose the output plan before transcoding begins.</p>
    </div>
    <ol class="lp-steps">${steps}</ol>
  </section>`;
}

function closingAction(): string {
  return `<section class="lp-closer" aria-labelledby="closer-title">
    <p class="lp-section-kicker">Ready when you are</p>
    <h2 class="lp-closer-title" id="closer-title">Turn one source into a complete playback package.</h2>
    <a href="/upload" class="btn btn-primary btn-lg">Upload video</a>
  </section>`;
}

export function mount(root: HTMLElement): () => void {
  const events = new AbortController();
  root.innerHTML = `${siteHeader()}
    <main id="main-content" class="lp-main">
      ${hero()}
      ${trustNotice()}
      ${workflow()}
      ${closingAction()}
    </main>
    ${siteFooter()}`;

  const playerMount = root.querySelector<HTMLElement>("#demo-player");
  const player = playerMount
    ? mountPlayer(playerMount, {
        playlist: "/demo/sintel/master.m3u8",
        poster: "/demo/sintel-cinematic-poster.webp",
        storyboard: DEMO_STORYBOARD,
        spriteUrl: "/demo/sintel-cinematic-storyboard.webp",
      })
    : null;

  root.querySelector("#watch-demo")?.addEventListener(
    "click",
    () => {
      const video = playerMount?.querySelector<HTMLVideoElement>("video");
      video?.focus({ preventScroll: true });
      void player?.play();
    },
    { signal: events.signal },
  );
  root.querySelectorAll<HTMLButtonElement>("[data-demo-quality]").forEach((button) => {
    button.addEventListener(
      "click",
      () => {
        const height = Number(button.dataset.demoQuality);
        if (!player?.selectQuality(height)) return;
        root.querySelectorAll("[data-demo-quality]").forEach((item) =>
          item.setAttribute("aria-pressed", String(item === button)),
        );
      },
      { signal: events.signal },
    );
  });

  return () => {
    events.abort();
    player?.destroy();
  };
}
