import { beforeEach, describe, expect, it } from "vitest";
import { mountNotFound, mountPrivacy, mountTerms } from "./legal";

let root: HTMLElement;

beforeEach(() => {
  document.body.innerHTML = '<div id="app"></div>';
  root = document.querySelector("#app") as HTMLElement;
});

describe("legal and recovery pages", () => {
  it("explains the guest session, temporary storage, and public share boundary", () => {
    mountPrivacy(root);

    expect(root.querySelector("h1")?.textContent).toBe("Privacy");
    expect(root.textContent).toContain("browser session");
    expect(root.textContent).toContain("temporary");
    expect(root.textContent).toContain("Anyone with a shared link");
    expect(root.querySelector('a[href="/upload"]')).not.toBeNull();
  });

  it("states the public demo limitations and acceptable-use boundary", () => {
    mountTerms(root);

    expect(root.querySelector("h1")?.textContent).toBe("Terms");
    expect(root.textContent).toContain("public demonstration service");
    expect(root.textContent).toContain("lawful content");
    expect(root.textContent).toContain("without notice");
  });

  it("renders the complete branded shell and useful recovery links for unknown routes", () => {
    mountNotFound(root);

    expect(root.querySelector("header")).not.toBeNull();
    expect(root.querySelector("footer")).not.toBeNull();
    expect(root.querySelector("h1")?.textContent).toBe("Page not found");
    expect(root.querySelector('a[href="/upload"]')).not.toBeNull();
    expect(root.querySelector('a[href="/history"]')).not.toBeNull();
  });
});
