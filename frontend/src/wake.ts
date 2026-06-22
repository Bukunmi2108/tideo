import { apiBase } from "./api";

type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export interface WaitForBackendReadyOptions {
  signal?: AbortSignal;
  onAttempt?: (attempt: number) => void;
  maxElapsedMs?: number;
  initialProbeMs?: number;
  probeTimeoutMs?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  fetcher?: FetchLike;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  now?: () => number;
}

const MAX_ELAPSED_MS = 90_000;
const INITIAL_PROBE_MS = 1_500;
const PROBE_TIMEOUT_MS = 10_000;
const BASE_DELAY_MS = 1_500;
const MAX_DELAY_MS = 8_000;

export async function waitForBackendReady(
  opts: WaitForBackendReadyOptions = {},
): Promise<boolean> {
  const signal = opts.signal;
  const fetcher = opts.fetcher ?? fetch;
  const sleep = opts.sleep ?? delay;
  const now = opts.now ?? Date.now;
  const maxElapsedMs = opts.maxElapsedMs ?? MAX_ELAPSED_MS;
  const initialProbeMs = opts.initialProbeMs ?? INITIAL_PROBE_MS;
  const probeTimeoutMs = opts.probeTimeoutMs ?? PROBE_TIMEOUT_MS;
  const baseDelayMs = opts.baseDelayMs ?? BASE_DELAY_MS;
  const maxDelayMs = opts.maxDelayMs ?? MAX_DELAY_MS;
  const started = now();
  let attempt = 0;

  while (!signal?.aborted && now() - started < maxElapsedMs) {
    opts.onAttempt?.(attempt);
    const remaining = maxElapsedMs - (now() - started);
    const timeout = Math.min(
      attempt === 0 ? initialProbeMs : probeTimeoutMs,
      remaining,
    );
    if (timeout <= 0) break;
    if (await probeReady(fetcher, timeout, signal)) return true;

    attempt++;
    const remainingAfterProbe = maxElapsedMs - (now() - started);
    if (remainingAfterProbe <= 0) break;
    const backoff = Math.min(
      baseDelayMs * 2 ** Math.max(0, attempt - 1),
      maxDelayMs,
      remainingAfterProbe,
    );
    await sleep(backoff, signal);
  }

  return false;
}

async function probeReady(
  fetcher: FetchLike,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<boolean> {
  const ctrl = new AbortController();
  const abort = () => ctrl.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, timeoutMs);

  try {
    const resp = await fetcher(`${apiBase()}/readyz`, {
      cache: "no-store",
      signal: ctrl.signal,
    });
    if (!resp.ok) return false;
    const body = (await resp.json().catch(() => null)) as {
      ready?: unknown;
    } | null;
    return body?.ready !== false;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}
