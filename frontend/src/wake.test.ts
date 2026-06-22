import { describe, expect, it } from "vitest";
import { waitForBackendReady } from "./wake";

function response(ok: boolean, body: unknown = { ready: ok }): Response {
  return {
    ok,
    status: ok ? 200 : 503,
    json: async () => body,
  } as Response;
}

describe("waitForBackendReady", () => {
  it("returns ready after a successful readiness probe", async () => {
    const attempts: number[] = [];
    const calls: string[] = [];

    const ready = await waitForBackendReady({
      onAttempt: (attempt) => attempts.push(attempt),
      fetcher: async (url) => {
        calls.push(String(url));
        return response(true, { ready: true });
      },
    });

    expect(ready).toBe(true);
    expect(attempts).toEqual([0]);
    expect(calls[0]).toMatch(/\/readyz$/);
  });

  it("retries non-ready probes until the backend wakes", async () => {
    let fetches = 0;
    let clock = 0;
    const attempts: number[] = [];
    const sleeps: number[] = [];

    const ready = await waitForBackendReady({
      maxElapsedMs: 1_000,
      initialProbeMs: 10,
      probeTimeoutMs: 10,
      baseDelayMs: 100,
      maxDelayMs: 1_000,
      now: () => clock,
      sleep: async (ms) => {
        sleeps.push(ms);
        clock += ms;
      },
      onAttempt: (attempt) => attempts.push(attempt),
      fetcher: async () => response(++fetches >= 3, { ready: fetches >= 3 }),
    });

    expect(ready).toBe(true);
    expect(attempts).toEqual([0, 1, 2]);
    expect(sleeps).toEqual([100, 200]);
  });

  it("returns false when the backend never becomes ready in time", async () => {
    let clock = 0;

    const ready = await waitForBackendReady({
      maxElapsedMs: 250,
      initialProbeMs: 10,
      probeTimeoutMs: 10,
      baseDelayMs: 100,
      maxDelayMs: 1_000,
      now: () => clock,
      sleep: async (ms) => {
        clock += ms;
      },
      fetcher: async () => response(false, { ready: false }),
    });

    expect(ready).toBe(false);
  });
});
