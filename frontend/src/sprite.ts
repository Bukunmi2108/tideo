// Scrub previews powered by Tideo's own generated sprite sheet. One sheet is a cols×rows grid of
// thumbnails sampled across the video; we shift background-position to land on a given tile.
import { apiBase, getStoryboard, type Storyboard } from "./api";

export function spriteUrl(jobId: string): string {
  return `${apiBase()}/jobs/${encodeURIComponent(jobId)}/sprite`;
}

function tilePosition(sb: Storyboard, i: number): string {
  const idx = Math.max(0, Math.min(sb.tiles - 1, i));
  const col = idx % sb.cols;
  const row = Math.floor(idx / sb.cols);
  const x = sb.cols > 1 ? (col / (sb.cols - 1)) * 100 : 0;
  const y = sb.rows > 1 ? (row / (sb.rows - 1)) * 100 : 0;
  return `${x}% ${y}%`;
}

// Turn an element into a single-tile window onto the sheet (the element gets the tile's aspect ratio).
export function applySprite(
  el: HTMLElement,
  sb: Storyboard,
  url: string,
): void {
  el.style.backgroundImage = `url('${url}')`;
  el.style.backgroundSize = `${sb.cols * 100}% ${sb.rows * 100}%`;
  el.style.backgroundRepeat = "no-repeat";
  el.style.backgroundPosition = tilePosition(sb, 0);
}

export function showTile(el: HTMLElement, sb: Storyboard, i: number): void {
  el.style.backgroundPosition = tilePosition(sb, i);
}

export function tileForFraction(sb: Storyboard, f: number): number {
  return Math.floor(Math.max(0, Math.min(0.999, f)) * sb.tiles);
}

export interface Loop {
  stop(): void;
}

// Flip through every tile at a steady rate — the card "plays" while hovered.
export function playLoop(el: HTMLElement, sb: Storyboard, fps = 6): Loop {
  let i = 0;
  const id = window.setInterval(() => {
    i = (i + 1) % sb.tiles;
    showTile(el, sb, i);
  }, 1000 / fps);
  return {
    stop() {
      window.clearInterval(id);
      showTile(el, sb, 0);
    },
  };
}

// Lazy-load a job's storyboard, caching the promise so repeated hovers don't refetch.
const cache = new Map<string, Promise<Storyboard | null>>();
export function loadStoryboard(jobId: string): Promise<Storyboard | null> {
  let p = cache.get(jobId);
  if (!p) {
    p = getStoryboard(jobId).catch(() => null); // 404 on pre-storyboard jobs → graceful no-scrub
    cache.set(jobId, p);
  }
  return p;
}
