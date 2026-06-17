import "./style.css"

// Phase 5.4 / 5.5 — inspect/commit + progress/player screen.
// Stub: renders the job ID from the URL so the page is navigable.

const appEl = document.getElementById("app")!
const jobId = new URLSearchParams(location.search).get("id")

appEl.innerHTML = `
  <header class="site-header">
    <a href="/" class="wordmark">tideo</a>
  </header>
  <main class="job-main" style="padding-top: 64px; text-align: center; color: var(--text-dim); font-size: 14px;">
    ${jobId ? `Job <code style="font-family: var(--font-mono)">${jobId}</code> — progress screen coming in 5.4/5.5` : "No job ID in URL"}
  </main>
`
