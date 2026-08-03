import Hls from "hls.js";
import type { Storyboard } from "./api";
import { icon } from "./icons";
import { esc, humanDuration } from "./render";
import { applySprite, showTile, tileForFraction } from "./sprite";

export interface PlayerHandle {
  destroy(): void;
  reload(): void;
}

interface Level {
  level: number;
  label: string;
}

interface PlayerOptions {
  playlist: string;
  poster?: string;
  storyboard?: Storyboard | null;
  spriteUrl?: string;
}

const CONTROL_TIMEOUT = 2600;

/** Mount Tideo's HLS player. The video never starts without an explicit user action. */
export function mountPlayer(
  container: HTMLElement,
  opts: PlayerOptions,
): PlayerHandle {
  container.classList.add("player", "player--controls-visible");
  container.innerHTML = `
    <video class="player-video" playsinline tabindex="0" ${opts.poster ? `poster="${esc(opts.poster)}"` : ""}></video>
    <button class="pl-center-play" type="button" aria-label="Play video">${icon("play")}</button>
    <div class="player-error" role="alert" hidden>
      <p class="player-error-copy"></p>
      <button class="btn btn-ghost player-retry" type="button">${icon("retry")} Retry playback</button>
    </div>
    <div class="pl-preview" hidden><div class="pl-preview-img"></div><span class="pl-preview-time">0:00</span></div>
    <div class="player-chrome">
      <button class="pl-btn pl-play" type="button" aria-label="Play">${icon("play")}</button>
      <input class="pl-seek" type="range" min="0" max="1000" value="0" aria-label="Seek" />
      <span class="pl-time">0:00 / 0:00</span>
      <div class="pl-volume">
        <button class="pl-btn pl-mute" type="button" aria-label="Mute">${icon("speaker")}</button>
        <input class="pl-vol" type="range" min="0" max="100" value="100" aria-label="Volume" />
      </div>
      <label class="pl-quality">
        <span class="sr-only">Playback quality</span>
        <select class="pl-quality-select" aria-label="Playback quality"><option value="-1">Auto</option></select>
      </label>
      <button class="pl-btn pl-cc" type="button" aria-label="Captions" aria-pressed="false" hidden>${icon("captions")}</button>
      <button class="pl-btn pl-full" type="button" aria-label="Enter fullscreen">${icon("fullscreen")}</button>
    </div>
  `;

  const video = container.querySelector<HTMLVideoElement>(".player-video")!;
  const centerPlay = container.querySelector<HTMLButtonElement>(".pl-center-play")!;
  const playBtn = container.querySelector<HTMLButtonElement>(".pl-play")!;
  const seek = container.querySelector<HTMLInputElement>(".pl-seek")!;
  const timeEl = container.querySelector<HTMLSpanElement>(".pl-time")!;
  const volumeWrap = container.querySelector<HTMLDivElement>(".pl-volume")!;
  const muteBtn = container.querySelector<HTMLButtonElement>(".pl-mute")!;
  const volume = container.querySelector<HTMLInputElement>(".pl-vol")!;
  const quality = container.querySelector<HTMLSelectElement>(".pl-quality-select")!;
  const qualityWrap = container.querySelector<HTMLElement>(".pl-quality")!;
  const captions = container.querySelector<HTMLButtonElement>(".pl-cc")!;
  const fullscreen = container.querySelector<HTMLButtonElement>(".pl-full")!;
  const error = container.querySelector<HTMLDivElement>(".player-error")!;
  const errorCopy = container.querySelector<HTMLElement>(".player-error-copy")!;
  const retry = container.querySelector<HTMLButtonElement>(".player-retry")!;
  const preview = container.querySelector<HTMLDivElement>(".pl-preview")!;
  const previewImg = container.querySelector<HTMLDivElement>(".pl-preview-img")!;
  const previewTime = container.querySelector<HTMLSpanElement>(".pl-preview-time")!;

  const events = new AbortController();
  let hls: Hls | null = null;
  let levels: Level[] = [{ level: -1, label: "Auto" }];
  let controlsTimer: number | null = null;
  let seeking = false;
  let destroyed = false;

  function listen(
    target: EventTarget,
    type: string,
    listener: EventListener,
  ): void {
    target.addEventListener(type, listener, { signal: events.signal });
  }

  function clearControlsTimer(): void {
    if (controlsTimer !== null) window.clearTimeout(controlsTimer);
    controlsTimer = null;
  }

  function showControls(keepVisible = false): void {
    clearControlsTimer();
    container.classList.add("player--controls-visible");
    if (!keepVisible && !video.paused && error.hidden) {
      controlsTimer = window.setTimeout(() => {
        if (!container.contains(document.activeElement))
          container.classList.remove("player--controls-visible");
      }, CONTROL_TIMEOUT);
    }
  }

  function showError(message: string): void {
    clearControlsTimer();
    errorCopy.textContent = message;
    error.hidden = false;
    container.classList.add("player--controls-visible");
  }

  function hideError(): void {
    error.hidden = true;
    errorCopy.textContent = "";
  }

  function renderLevels(): void {
    const selected = quality.value || "-1";
    quality.innerHTML = levels
      .map((item) => `<option value="${item.level}">${esc(item.label)}</option>`)
      .join("");
    quality.value = levels.some((item) => String(item.level) === selected)
      ? selected
      : "-1";
  }

  function nativeTracks(): TextTrack[] {
    return Array.from(video.textTracks ?? []);
  }

  function syncCaptions(): void {
    const hlsTracks = hls?.subtitleTracks ?? [];
    const tracks = nativeTracks();
    const hasCaptions = hlsTracks.length > 0 || tracks.length > 0;
    captions.hidden = !hasCaptions;
    if (!hasCaptions) return;
    const enabled = hls
      ? hls.subtitleTrack >= 0
      : tracks.some((track) => track.mode === "showing");
    captions.setAttribute("aria-pressed", String(enabled));
    captions.classList.toggle("pl-cc-on", enabled);
  }

  function toggleCaptions(): void {
    if (hls && hls.subtitleTracks.length > 0) {
      const enabled = hls.subtitleTrack >= 0;
      hls.subtitleDisplay = !enabled;
      hls.subtitleTrack = enabled ? -1 : 0;
    } else {
      const tracks = nativeTracks();
      const enabled = tracks.some((track) => track.mode === "showing");
      tracks.forEach((track, index) => {
        track.mode = !enabled && index === 0 ? "showing" : "disabled";
      });
    }
    syncCaptions();
  }

  function syncPlay(): void {
    const paused = video.paused;
    playBtn.innerHTML = icon(paused ? "play" : "pause");
    playBtn.setAttribute("aria-label", paused ? "Play" : "Pause");
    centerPlay.hidden = !paused;
    container.classList.toggle("player--playing", !paused);
    showControls(paused);
  }

  function syncVolume(): void {
    const muted = video.muted || video.volume === 0;
    muteBtn.innerHTML = icon(muted ? "muted" : "speaker");
    muteBtn.setAttribute("aria-label", muted ? "Unmute" : "Mute");
    muteBtn.setAttribute("aria-pressed", String(muted));
    if (!video.muted) volume.value = String(Math.round(video.volume * 100));
  }

  function syncTime(): void {
    if (!seeking && Number.isFinite(video.duration) && video.duration > 0)
      seek.value = String((video.currentTime / video.duration) * 1000);
    timeEl.textContent = `${humanDuration(video.currentTime)} / ${humanDuration(video.duration)}`;
  }

  async function togglePlayback(): Promise<void> {
    try {
      if (video.paused) await video.play();
      else video.pause();
    } catch {
      showError("Playback could not start. Check your connection and try again.");
    }
  }

  function attachTransport(): void {
    hls?.destroy();
    hls = null;
    hideError();
    if (Hls.isSupported()) {
      const instance = new Hls({ enableWorker: true });
      hls = instance;
      let recoveryAttempts = 0;
      instance.loadSource(opts.playlist);
      instance.attachMedia(video);
      instance.on(Hls.Events.MANIFEST_PARSED, () => {
        if (destroyed || hls !== instance) return;
        levels = [
          { level: -1, label: "Auto" },
          ...instance.levels.map((level, index) => ({
            level: index,
            label: `${level.height}p`,
          })),
        ];
        instance.subtitleTrack = -1;
        instance.subtitleDisplay = false;
        renderLevels();
        syncCaptions();
      });
      instance.on(Hls.Events.SUBTITLE_TRACKS_UPDATED, syncCaptions);
      instance.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal || hls !== instance) return;
        if (
          recoveryAttempts < 3 &&
          data.type === Hls.ErrorTypes.NETWORK_ERROR
        ) {
          recoveryAttempts += 1;
          instance.startLoad();
        } else if (
          recoveryAttempts < 3 &&
          data.type === Hls.ErrorTypes.MEDIA_ERROR
        ) {
          recoveryAttempts += 1;
          instance.recoverMediaError();
        } else {
          instance.destroy();
          hls = null;
          showError("This stream is unavailable or may have expired.");
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = opts.playlist;
      qualityWrap.hidden = true;
    } else {
      qualityWrap.hidden = true;
      showError("This browser can’t play HLS streams.");
    }
  }

  const storyboard = opts.storyboard;
  if (storyboard && opts.spriteUrl) {
    applySprite(previewImg, storyboard, opts.spriteUrl);
    previewImg.style.aspectRatio = `${storyboard.tile_w} / ${storyboard.tile_h}`;
    listen(seek, "pointermove", ((event: PointerEvent) => {
      const rect = seek.getBoundingClientRect();
      if (rect.width === 0) return;
      const fraction = Math.max(
        0,
        Math.min(1, (event.clientX - rect.left) / rect.width),
      );
      showTile(previewImg, storyboard, tileForFraction(storyboard, fraction));
      const duration =
        Number.isFinite(video.duration) && video.duration > 0
          ? video.duration
          : storyboard.interval * storyboard.tiles;
      previewTime.textContent = humanDuration(fraction * duration);
      const half = preview.offsetWidth / 2;
      preview.style.left = `${Math.max(half + 8, Math.min(rect.width - half - 8, event.clientX - rect.left))}px`;
      preview.hidden = false;
    }) as EventListener);
    listen(seek, "pointerleave", () => {
      preview.hidden = true;
    });
  }

  listen(playBtn, "click", () => void togglePlayback());
  listen(centerPlay, "click", () => void togglePlayback());
  listen(video, "click", () => void togglePlayback());
  listen(video, "play", syncPlay);
  listen(video, "pause", syncPlay);
  listen(video, "ended", syncPlay);
  listen(video, "timeupdate", syncTime);
  listen(video, "loadedmetadata", () => {
    syncTime();
    syncCaptions();
    const audioTracks = (
      video as HTMLVideoElement & { audioTracks?: { length: number } }
    ).audioTracks;
    if (audioTracks && audioTracks.length === 0) volumeWrap.hidden = true;
  });
  if (video.textTracks)
    listen(video.textTracks, "addtrack", syncCaptions);
  listen(video, "volumechange", syncVolume);
  listen(video, "error", () => {
    if (!hls) showError("This stream is unavailable or may have expired.");
  });
  listen(seek, "input", () => {
    seeking = true;
  });
  listen(seek, "change", () => {
    if (Number.isFinite(video.duration) && video.duration > 0)
      video.currentTime = (Number(seek.value) / 1000) * video.duration;
    seeking = false;
    syncTime();
  });
  listen(volume, "input", () => {
    video.muted = false;
    video.volume = Number(volume.value) / 100;
    syncVolume();
  });
  listen(muteBtn, "click", () => {
    video.muted = !video.muted;
    syncVolume();
  });
  listen(quality, "change", () => {
    if (hls) hls.currentLevel = Number(quality.value);
  });
  listen(captions, "click", toggleCaptions);
  listen(fullscreen, "click", () => {
    if (document.fullscreenElement) void document.exitFullscreen?.();
    else void container.requestFullscreen?.();
  });
  listen(document, "fullscreenchange", () => {
    const active = document.fullscreenElement === container;
    fullscreen.setAttribute(
      "aria-label",
      active ? "Exit fullscreen" : "Enter fullscreen",
    );
  });
  listen(container, "pointermove", () => showControls());
  listen(container, "pointerdown", () => showControls());
  listen(container, "focusin", () => showControls(true));
  listen(container, "focusout", () => showControls());
  listen(video, "keydown", ((event: KeyboardEvent) => {
    const key = event.key.toLowerCase();
    if (key === " " || key === "k") {
      event.preventDefault();
      void togglePlayback();
    } else if (key === "arrowleft" || key === "arrowright") {
      event.preventDefault();
      const delta = key === "arrowleft" ? -5 : 5;
      video.currentTime = Math.max(
        0,
        Math.min(video.duration || Infinity, video.currentTime + delta),
      );
      syncTime();
    } else if (key === "m") {
      event.preventDefault();
      video.muted = !video.muted;
      syncVolume();
    } else if (key === "c" && !captions.hidden) {
      event.preventDefault();
      toggleCaptions();
    } else if (key === "f") {
      event.preventDefault();
      fullscreen.click();
    }
  }) as EventListener);
  listen(retry, "click", attachTransport);

  attachTransport();
  syncPlay();
  syncVolume();

  return {
    reload() {
      if (destroyed) return;
      hideError();
      if (hls) hls.loadSource(opts.playlist);
      else if (video.src) video.load();
      else attachTransport();
    },
    destroy() {
      destroyed = true;
      clearControlsTimer();
      events.abort();
      hls?.destroy();
      hls = null;
      video.removeAttribute("src");
      container.replaceChildren();
      container.classList.remove(
        "player",
        "player--controls-visible",
        "player--playing",
      );
    },
  };
}
