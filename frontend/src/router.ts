// History-API router: intercepts same-origin <a> clicks + popstate, exposes navigate().

export interface RouteMeta {
  title: string;
  description: string;
}

const FALLBACK_META: RouteMeta = {
  title: "Page not found | Tideo",
  description: "The requested Tideo page could not be found.",
};

const ROUTE_META: Record<string, RouteMeta> = {
  "/": {
    title: "Tideo | Adaptive video, on demand",
    description: "Create adaptive HLS, a web-ready MP4, captions, and embeds from one source video.",
  },
  "/upload": {
    title: "Upload | Tideo",
    description: "Upload one video to inspect it and choose playback outputs before transcoding starts.",
  },
  "/job": {
    title: "Video job | Tideo",
    description: "Inspect, configure, and follow a Tideo video transcode.",
  },
  "/history": {
    title: "My videos | Tideo",
    description: "Review the videos created in this browser's guest session.",
  },
  "/privacy": {
    title: "Privacy | Tideo",
    description: "Learn how Tideo handles guest sessions, temporary media, and public watch links.",
  },
  "/terms": {
    title: "Terms | Tideo",
    description: "Review the acceptable-use and availability terms for the public Tideo service.",
  },
};

export function routeMeta(pathname: string): RouteMeta {
  const normalized = pathname.length > 1 ? pathname.replace(/\/$/, "") : pathname;
  return ROUTE_META[normalized] ?? FALLBACK_META;
}

function routeAnnouncer(): HTMLElement {
  let announcer = document.getElementById("route-announcer");
  if (!announcer) {
    announcer = document.createElement("div");
    announcer.id = "route-announcer";
    announcer.className = "sr-only";
    announcer.setAttribute("aria-live", "polite");
    announcer.setAttribute("aria-atomic", "true");
    document.body.appendChild(announcer);
  }
  return announcer;
}

export function applyRouteChrome(
  root: HTMLElement,
  meta: RouteMeta,
  isNavigation: boolean,
): void {
  document.title = meta.title;
  document.querySelector('meta[name="description"]')?.setAttribute("content", meta.description);
  const main = root.querySelector("main");
  if (main) {
    main.id = "main-content";
    main.tabIndex = -1;
  }
  if (!isNavigation) return;
  const heading = root.querySelector<HTMLElement>("h1") ?? main;
  if (heading) {
    heading.tabIndex = -1;
    heading.focus();
  }
  routeAnnouncer().textContent = meta.title;
}

type RouteHandler = (isNavigation: boolean) => void;
let onChange: RouteHandler = () => {};

export function startRouter(handler: RouteHandler): void {
  onChange = handler;
  document.addEventListener("click", onClick);
  window.addEventListener("popstate", onPopState);
  onChange(false);
}

export function navigate(path: string): void {
  if (path !== location.pathname + location.search)
    history.pushState(null, "", path);
  onChange(true);
}

function onPopState(): void {
  onChange(true);
}

function onClick(e: MouseEvent): void {
  if (
    e.defaultPrevented ||
    e.button !== 0 ||
    e.metaKey ||
    e.ctrlKey ||
    e.shiftKey ||
    e.altKey
  )
    return;
  const a = (e.target as HTMLElement).closest("a");
  const href = a?.getAttribute("href");
  if (!a || !href || a.target === "_blank" || a.hasAttribute("download"))
    return;
  if (href.startsWith("#")) {
    const target = document.querySelector<HTMLElement>(href);
    if (!target) return;
    e.preventDefault();
    history.replaceState(null, "", `${location.pathname}${location.search}${href}`);
    target.focus();
    target.scrollIntoView();
    return;
  }
  if (!href.startsWith("/")) return; // external/absolute → browser handles it
  e.preventDefault();
  navigate(href);
}
