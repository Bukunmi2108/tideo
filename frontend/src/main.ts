import "./style.css";
import { applyRouteChrome, routeMeta, startRouter } from "./router";
import { mount as mountLanding } from "./landing";
import { mount as mountUpload } from "./upload";
import { mount as mountJob } from "./job";
import { mount as mountHistory } from "./history";
import { mountNotFound, mountPrivacy, mountTerms } from "./legal";

// SPA entry: mounts one page into #app at a time, running the previous page's
// teardown first so watchers/timers/player don't leak across routes.

type Mounter = (root: HTMLElement, query: URLSearchParams) => () => void;

const routes: [RegExp, Mounter][] = [
  [/^\/$/, mountLanding],
  [/^\/upload\/?$/, mountUpload],
  [/^\/job\/?$/, mountJob],
  [/^\/history\/?$/, mountHistory],
  [/^\/privacy\/?$/, mountPrivacy],
  [/^\/terms\/?$/, mountTerms],
];

const app = document.getElementById("app")!;
let teardown: (() => void) | null = null;

function render(isNavigation: boolean): void {
  teardown?.();
  teardown = null;
  app.replaceChildren();
  const match = routes.find(([re]) => re.test(location.pathname));
  const query = new URLSearchParams(location.search);
  teardown = match ? match[1](app, query) : mountNotFound(app);
  applyRouteChrome(app, routeMeta(location.pathname), isNavigation);
}

startRouter(render);
