import { siteFooter, siteHeader } from "./render";

type PageMount = (root: HTMLElement) => () => void;

function legalPage(title: string, intro: string, body: string): string {
  return `${siteHeader()}
    <main id="main-content" class="legal-main">
      <article class="legal-document">
        <header class="legal-header">
          <p class="legal-kicker">Tideo public service</p>
          <h1>${title}</h1>
          <p class="legal-intro">${intro}</p>
          <p class="legal-updated">Effective 3 August 2026</p>
        </header>
        <div class="legal-body">${body}</div>
      </article>
    </main>
    ${siteFooter()}`;
}

export const mountPrivacy: PageMount = (root) => {
  root.innerHTML = legalPage(
    "Privacy",
    "How the public Tideo service handles your browser session and uploaded media.",
    `<section>
      <h2>Your browser session</h2>
      <p>Tideo creates a random guest-session credential and stores it in this browser. It identifies your uploads without creating an account. Clearing site data removes this browser's access to its My videos list.</p>
    </section>
    <section>
      <h2>Uploaded media</h2>
      <p>Files and generated outputs are temporary. They may expire and be deleted automatically. Do not use the public service for confidential, regulated, or sensitive material.</p>
    </section>
    <section>
      <h2>Shared links</h2>
      <p>Anyone with a shared link can watch the associated output until it expires. Guest-session credentials are not included in watch links or media URLs.</p>
    </section>
    <section>
      <h2>Service data</h2>
      <p>Operational logs may record technical request data needed to secure, diagnose, and operate the service. Tideo does not provide permanent media storage.</p>
    </section>
    <div class="legal-actions"><a class="btn btn-primary" href="/upload">Upload video</a><a class="btn btn-ghost" href="/history">My videos</a></div>`,
  );
  return () => {};
};

export const mountTerms: PageMount = (root) => {
  root.innerHTML = legalPage(
    "Terms",
    "The practical rules for using this temporary public demonstration service.",
    `<section>
      <h2>Acceptable use</h2>
      <p>Only upload lawful content that you own or have permission to process. Do not use Tideo to infringe rights, distribute harmful material, probe the service, or interfere with other visitors.</p>
    </section>
    <section>
      <h2>Temporary service</h2>
      <p>Tideo is a public demonstration service. Processing capacity and output availability are not guaranteed. Jobs, media, and shared links may expire or be removed without notice.</p>
    </section>
    <section>
      <h2>Your responsibility</h2>
      <p>Keep your original files and verify generated outputs before relying on them. You are responsible for the files you upload and the links you choose to share.</p>
    </section>
    <section>
      <h2>Service protection</h2>
      <p>Access may be limited or blocked to protect Tideo, its infrastructure, or other visitors. The open-source repository documents the software, but it does not promise hosted-service availability.</p>
    </section>
    <div class="legal-actions"><a class="btn btn-primary" href="/upload">Upload video</a><a class="btn btn-ghost" href="/">Overview</a></div>`,
  );
  return () => {};
};

export const mountNotFound: PageMount = (root) => {
  root.innerHTML = `${siteHeader()}
    <main id="main-content" class="system-main">
      <section class="state-panel" aria-labelledby="not-found-title">
        <p class="state-code">404</p>
        <h1 id="not-found-title">Page not found</h1>
        <p>The address may be outdated, or the page may have moved. Start a new upload or return to your videos.</p>
        <div class="state-actions">
          <a href="/upload" class="btn btn-primary">Upload video</a>
          <a href="/history" class="btn btn-ghost">My videos</a>
        </div>
      </section>
    </main>
    ${siteFooter()}`;
  return () => {};
};
