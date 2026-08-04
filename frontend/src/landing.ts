import { siteFooter, siteHeader } from "./render";

const STEPS: [string, string][] = [
  ["Upload", "Choose one source video up to 4 GB."],
  ["Inspect", "Review the codec, resolution, duration, and suggested ladder."],
  ["Choose outputs", "Select playback sizes and optional captions before work begins."],
  ["Encode", "Tideo processes each selected rendition in parallel."],
  ["Stream", "Use adaptive HLS, download the MP4, or copy an embed."],
];

function outputProof(): string {
  return `<figure class="lp-proof" id="demo-output">
    <div class="lp-proof-frame">
      <img
        class="lp-proof-poster"
        src="/demo/tideo-test-pattern-poster.webp"
        width="1280"
        height="720"
        alt="A synthetic test pattern used to demonstrate a privacy-safe Tideo video output"
        fetchpriority="high"
      />
      <div class="lp-proof-frame-meta" aria-hidden="true">
        <span>Demo output</span>
        <span>00:12</span>
      </div>
    </div>
    <figcaption class="lp-proof-details">
      <div class="lp-proof-heading">
        <div>
          <p class="lp-proof-kicker">Synthetic fixture, no user media</p>
          <h2>One source, three playback sizes</h2>
        </div>
        <span class="status-badge status-badge--success">Ready</span>
      </div>
      <dl class="lp-proof-specs">
        <div><dt>Source</dt><dd>1280 × 720</dd></div>
        <div><dt>Measured bitrate</dt><dd>7.5 Mbps</dd></div>
        <div><dt>Codecs</dt><dd>H.264 / AAC</dd></div>
      </dl>
      <div class="lp-proof-outputs" aria-label="Generated output summary">
        <span>720p</span><span>480p</span><span>360p</span><span>240p</span><span>HLS</span><span>MP4</span>
      </div>
      <p class="lp-proof-caption">Captions: not requested for this synthetic demo.</p>
      <img
        class="lp-proof-storyboard"
        src="/demo/tideo-test-pattern-storyboard.webp"
        width="1280"
        height="360"
        alt="Eight frames from the generated test-pattern storyboard"
        loading="lazy"
      />
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
        <a href="#demo-output" class="btn btn-ghost btn-lg">Watch demo</a>
      </div>
      <p class="lp-limit">MP4, MOV, MKV, WebM, AVI, or M4V. Up to 4 GB.</p>
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
  root.innerHTML = `${siteHeader()}
    <main id="main-content" class="lp-main">
      ${hero()}
      ${trustNotice()}
      ${workflow()}
      ${closingAction()}
    </main>
    ${siteFooter()}`;

  return () => {};
}
