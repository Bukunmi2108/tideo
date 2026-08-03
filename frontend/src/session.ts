export const SESSION_HEADER = "X-Tideo-Session";
export const SESSION_STORAGE_KEY = "tideo.guest-session.v1";

const TOKEN_RE = /^v1\.[A-Za-z0-9_-]{43}$/;
let volatileSession: string | null = null;

function createSession(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `v1.${btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}`;
}

export function guestSession(): string {
  try {
    const stored = localStorage.getItem(SESSION_STORAGE_KEY);
    if (stored && TOKEN_RE.test(stored)) return stored;
    const created = createSession();
    localStorage.setItem(SESSION_STORAGE_KEY, created);
    volatileSession = created;
    return created;
  } catch {
    volatileSession ??= createSession();
    return volatileSession;
  }
}

export function sessionHeaders(): Record<string, string> {
  return { [SESSION_HEADER]: guestSession() };
}
