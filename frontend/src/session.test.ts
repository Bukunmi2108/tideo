import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  SESSION_HEADER,
  SESSION_STORAGE_KEY,
  guestSession,
  sessionHeaders,
} from "./session";

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    clear: () => values.clear(),
  });
});

describe("guestSession", () => {
  it("creates and persists a versioned 256-bit bearer token", () => {
    const token = guestSession();

    expect(token).toMatch(/^v1\.[A-Za-z0-9_-]{43}$/);
    expect(guestSession()).toBe(token);
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe(token);
  });

  it("replaces malformed stored data", () => {
    localStorage.setItem(SESSION_STORAGE_KEY, "not-a-session");

    expect(guestSession()).toMatch(/^v1\.[A-Za-z0-9_-]{43}$/);
    expect(guestSession()).not.toBe("not-a-session");
  });

  it("builds the owner request header", () => {
    const token = guestSession();

    expect(sessionHeaders()).toEqual({ [SESSION_HEADER]: token });
  });
});
