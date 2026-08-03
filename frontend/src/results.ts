import {
  apiBase,
  getManifest,
  type JobResults,
  type Manifest,
  type SubtitleStatus,
} from "./api";
import { icon } from "./icons";
import { esc, humanBitrate, humanDuration } from "./render";
import { loadStoryboard, spriteUrl } from "./sprite";
import type { PlayerHandle } from "./player";

export interface CompletedResultOptions {
  jobId: string;
  title: string;
  results: JobResults;
  expiresAt?: string | null;
}

export interface CompletedResultHandle {
  destroy(): void;
  updateSubtitles(subtitles: SubtitleStatus | null | undefined): void;
}

function captionDetail(subtitles: SubtitleStatus | null | undefined): string {
  if (!subtitles)
    return `<strong>Not requested</strong><span>Generate captions on a future upload when you need an accessible text track.</span>`;
  const copy = {
    processing: [
      "Generating",
      "Playback is ready now. Captions will appear here when transcription finishes.",
    ],
    ready: ["Ready", "Use the captions control in the player to turn the track on."],
    none: ["No speech found", "The audio did not contain speech Tideo could transcribe."],
    failed: ["Unavailable", "The video is ready, but caption generation did not finish."],
  }[subtitles.status];
  return `<strong>${copy[0]}</strong><span>${copy[1]}</span>`;
}

function expiryDetail(expiresAt: string | null | undefined): string {
  if (!expiresAt)
    return `<strong>Temporary</strong><span>Outputs expire automatically. Keep your original file.</span>`;
  const date = new Date(expiresAt);
  if (Number.isNaN(date.getTime()))
    return `<strong>Temporary</strong><span>Outputs expire automatically. Keep your original file.</span>`;
  const label = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
  return `<strong>Expires <time datetime="${esc(expiresAt)}">${esc(label)}</time></strong><span>The stream, download, and shared watch link expire together.</span>`;
}

function embedSnippet(playlistUrl: string): string {
  return `<video id="video" controls playsinline style="width:100%"></video>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1"></script>
<script>const hls=new Hls();hls.loadSource(${JSON.stringify(playlistUrl)});hls.attachMedia(document.getElementById("video"));</script>`;
}

export function renderCompletedResult(options: CompletedResultOptions): string {
  const { results } = options;
  const base = apiBase();
  const title = esc(options.title || options.jobId || "Your video");
  const renditions = results.presets?.length ?? 0;
  const meta = [
    results.duration != null ? humanDuration(results.duration) : "",
    renditions ? `${renditions} rendition${renditions === 1 ? "" : "s"}` : "",
    "adaptive HLS",
  ]
    .filter(Boolean)
    .join(" · ");
  const playlist = base + results.playlist;
  const download = base + results.web_mp4;

  return `<div class="watch">
    <section class="watch-stage" aria-label="Video player">
      <div class="player-mount" id="player-mount"></div>
      <div class="watch-overlay">
        <p class="watch-eyebrow">Ready to watch</p>
        <h1 class="watch-title">${title}</h1>
      </div>
      <a class="watch-scroll" href="#watch-detail">View output details</a>
    </section>
    <section class="watch-detail" id="watch-detail" aria-labelledby="output-title">
      <div class="watch-detail-inner">
        <div class="watch-head">
          <div>
            <p class="watch-kicker">Completed output</p>
            <h2 class="watch-name" id="output-title">${title}</h2>
            <p class="watch-meta"><span>${esc(meta)}</span><span class="watch-chip">Ready</span></p>
          </div>
          <div class="watch-actions">
            <button class="btn btn-primary" id="share-player" type="button">${icon("share")} Share video</button>
            <a class="btn btn-ghost" href="${esc(download)}" download>${icon("download")} Download MP4</a>
            <button class="btn btn-ghost" id="copy-master" type="button">${icon("copy")} Copy stream URL</button>
            <a class="btn btn-ghost" href="/upload">New upload</a>
          </div>
        </div>
        <div class="share-disclosure">
          <p>Anyone with the link can watch this video until the output expires.</p>
          <p class="copy-feedback" role="status" aria-live="polite" aria-atomic="true"></p>
        </div>
        <section class="output-details" aria-labelledby="details-title">
          <div class="output-details-head">
            <div>
              <p class="watch-kicker">Delivery package</p>
              <h2 id="details-title">Output details</h2>
            </div>
            <span class="output-job-id">Job ${esc(options.jobId)}</span>
          </div>
          <div class="output-detail-grid">
            <article class="output-detail">
              <h3>Stream</h3>
              <strong>Adaptive HLS</strong>
              <span>${renditions || "Multiple"} playback ${renditions === 1 ? "quality" : "qualities"}, selected automatically or manually.</span>
            </article>
            <article class="output-detail">
              <h3>Download</h3>
              <strong>Web-ready MP4</strong>
              <span>A single portable file for direct playback and publishing.</span>
            </article>
            <article class="output-detail caption-detail" data-caption-status="${results.subtitles?.status ?? "not-requested"}">
              <h3>Captions</h3>
              ${captionDetail(results.subtitles)}
            </article>
            <article class="output-detail retention-detail">
              <h3>Retention</h3>
              ${expiryDetail(options.expiresAt)}
            </article>
          </div>
        </section>
        <section class="ladder" id="ladder" aria-labelledby="ladder-title">
          <div class="ladder-head" id="ladder-title">Rendition ladder</div>
          <div class="ladder-rows" id="ladder-rows" aria-live="polite"><div class="ladder-loading">Reading manifest…</div></div>
        </section>
        <details class="disclosure embed-block">
          <summary>Developer embed</summary>
          <p class="embed-intro">Use this HLS source when integrating the temporary output into a web player.</p>
          <pre class="embed-code">${esc(embedSnippet(playlist))}</pre>
          <button class="btn btn-ghost" id="copy-embed" type="button">${icon("copy")} Copy embed code</button>
        </details>
      </div>
    </section>
  </div>`;
}

function renderLadder(manifest: Manifest): string {
  if (manifest.renditions.length === 0)
    return `<p class="ladder-loading">Rendition measurements are not available.</p>`;
  const rungs = [...manifest.renditions].sort(
    (left, right) => right.bandwidth - left.bandwidth,
  );
  const max = Math.max(1, ...rungs.map((rendition) => rendition.bandwidth));
  return rungs
    .map((rendition) => {
      const width = Math.round((rendition.bandwidth / max) * 100);
      const videoCodec = rendition.codecs.split(",")[0];
      return `<div class="rung">
        <span class="rung-label">${esc(rendition.preset)}</span>
        <span class="rung-res">${esc(rendition.resolution.replace("x", "×"))}</span>
        <div class="rung-bar" aria-hidden="true"><div class="rung-fill" style="width:${width}%"></div></div>
        <span class="rung-rate">${humanBitrate(rendition.bandwidth)}</span>
        <span class="rung-codec">${esc(videoCodec)}</span>
      </div>`;
    })
    .join("");
}

export function mountCompletedResult(
  root: HTMLElement,
  options: CompletedResultOptions,
): CompletedResultHandle {
  const events = new AbortController();
  const requests = new AbortController();
  const feedback = root.querySelector<HTMLElement>(".copy-feedback");
  let player: PlayerHandle | null = null;
  let feedbackTimer: number | null = null;
  let destroyed = false;
  let reloadForCaptions = false;

  function listen(
    selector: string,
    event: string,
    listener: EventListener,
  ): void {
    root
      .querySelector(selector)
      ?.addEventListener(event, listener, { signal: events.signal });
  }

  function announce(message: string): void {
    if (!feedback) return;
    if (feedbackTimer !== null) window.clearTimeout(feedbackTimer);
    feedback.textContent = message;
    feedbackTimer = window.setTimeout(() => {
      if (feedback.textContent === message) feedback.textContent = "";
    }, 4000);
  }

  async function copy(text: string, success: string): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(text);
      announce(success);
      return true;
    } catch {
      announce("Copy failed. Allow clipboard access and try again.");
      return false;
    }
  }

  const base = apiBase();
  const shareUrl = new URL(base + options.results.player, location.href).href;
  const playlistUrl = base + options.results.playlist;

  listen("#share-player", "click", () => {
    void (async () => {
      if (typeof navigator.share === "function") {
        try {
          await navigator.share({ title: options.title, url: shareUrl });
          announce("Share complete.");
          return;
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") return;
        }
      }
      await copy(shareUrl, "Share link copied.");
    })();
  });
  listen("#copy-master", "click", () => {
    void copy(playlistUrl, "Stream URL copied.");
  });
  listen("#copy-embed", "click", () => {
    void copy(embedSnippet(playlistUrl), "Embed code copied.");
  });

  const mount = root.querySelector<HTMLElement>("#player-mount");
  if (mount) {
    void Promise.all([
      loadStoryboard(options.jobId, requests.signal),
      import("./player"),
    ])
      .then(([storyboard, playerModule]) => {
        if (destroyed) return;
        mount.classList.add("player--stage");
        player = playerModule.mountPlayer(mount, {
          playlist: playlistUrl,
          poster: base + options.results.poster,
          storyboard,
          spriteUrl: spriteUrl(options.jobId),
        });
        if (reloadForCaptions) player.reload();
      })
      .catch(() => {
        if (destroyed) return;
        mount.classList.add("player", "player--stage");
        mount.innerHTML = `<div class="player-error player-load-error" role="alert"><p class="player-error-copy">The player couldn’t load. Refresh the page to try again.</p></div>`;
      });
  }

  void getManifest(options.jobId, requests.signal)
    .then((manifest) => {
      if (destroyed) return;
      const rows = root.querySelector<HTMLElement>("#ladder-rows");
      if (rows) rows.innerHTML = renderLadder(manifest);
    })
    .catch((error) => {
      if (
        destroyed ||
        (error instanceof DOMException && error.name === "AbortError")
      )
        return;
      const rows = root.querySelector<HTMLElement>("#ladder-rows");
      if (rows)
        rows.innerHTML = `<p class="ladder-loading">Rendition measurements are temporarily unavailable.</p>`;
    });

  return {
    updateSubtitles(subtitles) {
      const detail = root.querySelector<HTMLElement>(".caption-detail");
      if (!detail) return;
      detail.dataset.captionStatus = subtitles?.status ?? "not-requested";
      detail.innerHTML = `<h3>Captions</h3>${captionDetail(subtitles)}`;
      if (subtitles?.status === "ready") {
        if (player) player.reload();
        else reloadForCaptions = true;
      }
    },
    destroy() {
      destroyed = true;
      events.abort();
      requests.abort();
      if (feedbackTimer !== null) window.clearTimeout(feedbackTimer);
      player?.destroy();
      player = null;
    },
  };
}
