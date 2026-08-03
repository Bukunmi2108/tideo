import "./style.css";
import { applyRouteChrome, routeMeta, startRouter } from "./router";
import { siteFooter, siteHeader } from "./render";

type Mounter = (root: HTMLElement, query: URLSearchParams) => () => void;
type Loader = () => Promise<Mounter>;

const routes: Array<[RegExp, Loader]> = [
  [/^\/$/, async () => (await import("./landing")).mount],
  [/^\/upload\/?$/, async () => (await import("./upload")).mount],
  [/^\/job\/?$/, async () => (await import("./job")).mount],
  [/^\/history\/?$/, async () => (await import("./history")).mount],
  [/^\/privacy\/?$/, async () => (await import("./legal")).mountPrivacy],
  [/^\/terms\/?$/, async () => (await import("./legal")).mountTerms],
];

const app = document.getElementById("app")!;
let teardown: (() => void) | null = null;
let generation = 0;

async function render(isNavigation: boolean): Promise<void> {
  const current = ++generation;
  teardown?.();
  teardown = null;
  app.innerHTML = `<main id="main-content" class="route-loading" aria-busy="true"><span class="sr-only">Loading page</span></main>`;
  const match = routes.find(([pattern]) => pattern.test(location.pathname));
  const query = new URLSearchParams(location.search);
  try {
    const mount = match
      ? await match[1]()
      : (await import("./legal")).mountNotFound;
    if (current !== generation) return;
    app.replaceChildren();
    teardown = mount(app, query);
    applyRouteChrome(app, routeMeta(location.pathname), isNavigation);
  } catch {
    if (current !== generation) return;
    app.innerHTML = `${siteHeader()}
      <main id="main-content" class="legal-main">
        <article class="legal-card" role="alert">
          <p class="legal-eyebrow">Loading error</p>
          <h1 tabindex="-1">This page couldn’t load</h1>
          <p>A page asset may be temporarily unavailable. Check your connection and try again.</p>
          <div class="legal-actions"><button class="btn btn-primary route-retry" type="button">Try again</button><a class="btn btn-ghost" href="/">Overview</a></div>
        </article>
      </main>
      ${siteFooter()}`;
    app.querySelector(".route-retry")?.addEventListener(
      "click",
      () => void render(false),
      { once: true },
    );
    applyRouteChrome(app, routeMeta(location.pathname), isNavigation);
  }
}

startRouter((isNavigation) => void render(isNavigation));
