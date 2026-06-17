// History-API router: intercepts same-origin <a> clicks + popstate, exposes navigate().

let onChange: () => void = () => {};

export function startRouter(handler: () => void): void {
  onChange = handler;
  document.addEventListener("click", onClick);
  window.addEventListener("popstate", onChange);
  onChange();
}

export function navigate(path: string): void {
  if (path !== location.pathname + location.search)
    history.pushState(null, "", path);
  onChange();
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
  if (!href.startsWith("/")) return; // external/absolute → browser handles it
  e.preventDefault();
  navigate(href);
}
