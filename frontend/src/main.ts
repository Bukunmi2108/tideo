import "./style.css"
import { startRouter } from "./router"
import { mount as mountUpload } from "./upload"
import { mount as mountJob } from "./job"
import { mount as mountHistory } from "./history"

// SPA entry: mounts one page into #app at a time, running the previous page's
// teardown first so watchers/timers/player don't leak across routes.

type Mounter = (root: HTMLElement, query: URLSearchParams) => () => void

const routes: [RegExp, Mounter][] = [
  [/^\/$/, mountUpload],
  [/^\/job\/?$/, mountJob],
  [/^\/history\/?$/, mountHistory],
]

const app = document.getElementById("app")!
let teardown: (() => void) | null = null

function render(): void {
  teardown?.()
  teardown = null
  app.replaceChildren()
  const match = routes.find(([re]) => re.test(location.pathname))
  const query = new URLSearchParams(location.search)
  teardown = match ? match[1](app, query) : mountNotFound(app)
}

function mountNotFound(root: HTMLElement): () => void {
  root.innerHTML = `
    <main class="job-main">
      <div class="inspect-card inspect-card--terminal">
        <h1 class="inspect-title">Page not found</h1>
        <a href="/" class="btn btn-primary">Back to upload</a>
      </div>
    </main>`
  return () => {}
}

startRouter(render)
