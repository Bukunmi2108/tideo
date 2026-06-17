import Hls from "hls.js";
import { esc, humanDuration } from "./render";

// hls.js player with custom chrome. mount() expects origin-correct (absolute)
// URLs so the playlist's relative segment refs resolve against the API origin.

export interface PlayerHandle {
  destroy(): void;
}

interface Level {
  level: number; // -1 = auto
  label: string;
}

export function mountPlayer(
  container: HTMLElement,
  opts: { playlist: string; poster?: string },
): PlayerHandle {
  container.classList.add("player");
  container.innerHTML = `
    <video class="player-video" playsinline ${opts.poster ? `poster="${esc(opts.poster)}"` : ""}></video>
    <div class="player-error" hidden></div>
    <div class="player-chrome">
      <button class="pl-btn pl-play" aria-label="Play">▶</button>
      <input class="pl-seek" type="range" min="0" max="1000" value="0" aria-label="Seek" />
      <span class="pl-time">0:00 / 0:00</span>
      <input class="pl-vol" type="range" min="0" max="100" value="100" aria-label="Volume" />
      <div class="pl-quality">
        <button class="pl-btn pl-quality-btn" aria-haspopup="true" aria-expanded="false">Auto</button>
        <ul class="pl-quality-menu" role="menu" hidden></ul>
      </div>
      <button class="pl-btn pl-cc" aria-label="Subtitles" aria-pressed="false" hidden>CC</button>
      <button class="pl-btn pl-full" aria-label="Fullscreen">⛶</button>
    </div>
  `;

  const video = container.querySelector<HTMLVideoElement>(".player-video")!;
  const playBtn = container.querySelector<HTMLButtonElement>(".pl-play")!;
  const seek = container.querySelector<HTMLInputElement>(".pl-seek")!;
  const timeEl = container.querySelector<HTMLSpanElement>(".pl-time")!;
  const vol = container.querySelector<HTMLInputElement>(".pl-vol")!;
  const qualityWrap = container.querySelector<HTMLDivElement>(".pl-quality")!;
  const qualityBtn =
    container.querySelector<HTMLButtonElement>(".pl-quality-btn")!;
  const qualityMenu =
    container.querySelector<HTMLUListElement>(".pl-quality-menu")!;
  const ccBtn = container.querySelector<HTMLButtonElement>(".pl-cc")!;
  const fullBtn = container.querySelector<HTMLButtonElement>(".pl-full")!;
  const errEl = container.querySelector<HTMLDivElement>(".player-error")!;

  let hls: Hls | null = null;
  let levels: Level[] = [];
  let seeking = false;

  function showError(msg: string): void {
    errEl.textContent = msg;
    errEl.hidden = false;
  }

  // ---- subtitles (CC) ----
  // hls.js surfaces the master's SUBTITLES track once parsed. Default off; the CC button toggles it.
  // When transcription lands after the player mounts, the track shows up on the next track-update.
  function syncCc(): void {
    const has = !!hls && hls.subtitleTracks.length > 0;
    ccBtn.hidden = !has;
    if (!has) return;
    const on = hls!.subtitleTrack >= 0;
    ccBtn.setAttribute("aria-pressed", String(on));
    ccBtn.classList.toggle("pl-cc-on", on);
  }
  ccBtn.addEventListener("click", () => {
    if (!hls || hls.subtitleTracks.length === 0) return;
    const on = hls.subtitleTrack >= 0;
    hls.subtitleDisplay = !on;
    hls.subtitleTrack = on ? -1 : 0;
    syncCc();
  });

  // ---- transport ----
  if (Hls.isSupported()) {
    hls = new Hls({ enableWorker: true });
    hls.loadSource(opts.playlist);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      levels = [
        { level: -1, label: "Auto" },
        ...hls!.levels.map((l, i) => ({ level: i, label: `${l.height}p` })),
      ];
      hls!.subtitleTrack = -1; // start with captions off; the CC button opts in
      hls!.subtitleDisplay = false;
      renderQuality();
      syncCc();
    });
    hls.on(Hls.Events.SUBTITLE_TRACKS_UPDATED, syncCc); // track may appear after a late playlist rewrite
    hls.on(Hls.Events.LEVEL_SWITCHED, renderQuality); // ABR made visible — active level updates live
    // hls.js owns error handling in MSE mode; recover transient faults, only surface unrecoverable ones.
    let recovery = 0;
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (!data.fatal) return; // hls.js retries non-fatal errors itself
      console.error("hls.js fatal:", data.type, data.details);
      if (recovery < 3 && data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        recovery++;
        hls!.startLoad();
      } else if (recovery < 3 && data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        recovery++;
        hls!.recoverMediaError();
      } else {
        hls!.destroy();
        showError("Stream unavailable — the demo output may have expired.");
      }
    });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = opts.playlist; // Safari: native HLS, no quality menu
    qualityWrap.hidden = true;
    // native path: hls.js isn't here to manage errors, so listen on the element directly
    video.addEventListener("error", () =>
      showError("Stream unavailable — the demo output may have expired."),
    );
  } else {
    qualityWrap.hidden = true;
    showError("This browser can’t play HLS streams.");
  }

  // ---- chrome wiring ----
  function syncPlay(): void {
    playBtn.textContent = video.paused ? "▶" : "❚❚";
    playBtn.setAttribute("aria-label", video.paused ? "Play" : "Pause");
  }

  const onPlay = () => syncPlay();
  const onTime = () => {
    if (!seeking && video.duration)
      seek.value = String((video.currentTime / video.duration) * 1000);
    timeEl.textContent = `${humanDuration(video.currentTime)} / ${humanDuration(video.duration)}`;
  };

  playBtn.addEventListener(
    "click",
    () => void (video.paused ? video.play() : video.pause()),
  );
  video.addEventListener("play", onPlay);
  video.addEventListener("pause", onPlay);
  video.addEventListener("timeupdate", onTime);
  video.addEventListener("loadedmetadata", onTime);
  seek.addEventListener("input", () => {
    seeking = true;
  });
  seek.addEventListener("change", () => {
    if (video.duration)
      video.currentTime = (Number(seek.value) / 1000) * video.duration;
    seeking = false;
  });
  vol.addEventListener("input", () => {
    video.volume = Number(vol.value) / 100;
  });
  fullBtn.addEventListener("click", () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void container.requestFullscreen();
  });

  // ---- quality menu ----
  function renderQuality(): void {
    const active = hls ? hls.currentLevel : -1;
    qualityBtn.textContent =
      active === -1
        ? "Auto"
        : (levels.find((l) => l.level === active)?.label ?? "Auto");
    qualityMenu.innerHTML = levels
      .map(
        (l) =>
          `<li role="menuitemradio" aria-checked="${l.level === active}" data-level="${l.level}" tabindex="0">${l.label}</li>`,
      )
      .join("");
  }

  function closeMenu(): void {
    qualityMenu.hidden = true;
    qualityBtn.setAttribute("aria-expanded", "false");
  }

  qualityBtn.addEventListener("click", () => {
    const open = qualityMenu.hidden;
    qualityMenu.hidden = !open;
    qualityBtn.setAttribute("aria-expanded", String(open));
  });
  qualityMenu.addEventListener("click", (e) => {
    const li = (e.target as HTMLElement).closest<HTMLLIElement>(
      "li[data-level]",
    );
    if (!li || !hls) return;
    hls.currentLevel = Number(li.dataset.level); // -1 = auto; manual pin otherwise
    renderQuality();
    closeMenu();
  });
  const onDocClick = (e: MouseEvent) => {
    if (!qualityWrap.contains(e.target as Node)) closeMenu();
  };
  document.addEventListener("click", onDocClick);

  return {
    destroy() {
      document.removeEventListener("click", onDocClick);
      hls?.destroy();
      hls = null;
      container.replaceChildren();
      container.classList.remove("player");
    },
  };
}
