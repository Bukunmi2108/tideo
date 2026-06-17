"""Phase 8.3 — horizontal scaling benchmark.

Protocol: a fixed batch of identical jobs (unique-by-suffix so dedupe doesn't collapse them) is submitted
to the full ladder, run at increasing worker-heavy replica counts. The metric is wall-clock from the first
`job.created` to the last `job.completed`, read from the events audit table (3.4 earns its keep). Per-preset
encode ratios are measured from the renditions table to validate the capacity model.

Usage (from the host, stack up):
    uv run python scripts/scale_bench.py --scales 1,2,4 --runs 2 --batch 6 --fixture fixtures/bench_1080p.mp4
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # run as a script -> put repo root on path
from app.core.config import config

API = "http://localhost:8000"
PG_DSN = (f"postgresql://{config.postgres_user}:{config.postgres_password}"
          f"@127.0.0.1:{config.postgres_port}/{config.postgres_db}")


def _sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def scale_workers(n: int) -> None:
    print(f"  scaling worker-heavy -> {n} ...", flush=True)
    subprocess.run(["docker", "compose", "up", "-d", "--no-recreate", "--scale", f"worker-heavy={n}"],
                   capture_output=True, text=True)
    # wait until exactly n heavy@ nodes answer an inspect ping
    deadline = time.time() + 90
    while time.time() < deadline:
        out = _sh("docker", "compose", "exec", "-T", "worker-fast",
                  "celery", "-A", "app.workers.celery_app", "inspect", "ping", "--timeout", "3")
        if out.count("heavy@") == n:
            print(f"  {n} heavy worker(s) ready", flush=True)
            time.sleep(2)
            return
        time.sleep(3)
    print(f"  WARN: only {_sh('docker','compose','exec','-T','worker-fast','celery','-A','app.workers.celery_app','inspect','ping','--timeout','3').count('heavy@')} heavy workers answered", flush=True)


def submit_batch(fixture: bytes, batch: int) -> list[str]:
    job_ids = []
    with httpx.Client(timeout=30) as c:
        for _ in range(batch):
            body = fixture + os.urandom(16)             # unique content -> dedupe miss, identical decode
            r = c.post(f"{API}/upload", params={"filename": "bench.mp4"}, content=body)
            job_ids.append(r.json()["job_id"])
        # wait for every job to reach awaiting_choice, then fire the whole batch
        for jid in job_ids:
            for _ in range(60):
                if c.get(f"{API}/jobs/{jid}").json()["status"] == "awaiting_choice":
                    break
                time.sleep(1)
        for jid in job_ids:
            presets = c.get(f"{API}/jobs/{jid}").json()["recommended_presets"]
            c.post(f"{API}/jobs/{jid}/transcode", json={"presets": presets})
    return job_ids


def wait_terminal(job_ids: list[str], timeout: int = 1200) -> None:
    deadline = time.time() + timeout
    with httpx.Client(timeout=30) as c:
        while time.time() < deadline:
            states = [c.get(f"{API}/jobs/{j}").json().get("status") for j in job_ids]
            if all(s in ("done", "failed") for s in states):
                return
            time.sleep(2)
    print("  WARN: timed out waiting for batch to finish", flush=True)


def batch_walltime(job_ids: list[str]) -> tuple[float, int]:
    """Wall-clock first job.created -> last job.completed, from the events table. Returns (seconds, completed)."""
    conn = psycopg2.connect(PG_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM ("
                "  MAX(ts) FILTER (WHERE event_type='job.completed') -"
                "  MIN(ts) FILTER (WHERE event_type='job.created'))),"
                "  COUNT(*) FILTER (WHERE event_type='job.completed') "
                "FROM events WHERE job_id = ANY(%s)", (job_ids,))
            secs, completed = cur.fetchone()
            return float(secs or 0), int(completed or 0)
    finally:
        conn.close()


def preset_ratios(all_job_ids: list[str], duration_s: float) -> dict:
    """Per-preset encode ratio = mean(encode_seconds)/duration, measured from the renditions table."""
    conn = psycopg2.connect(PG_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT preset, AVG(encode_seconds) FROM renditions "
                        "WHERE job_id = ANY(%s) AND status='completed' GROUP BY preset", (all_job_ids,))
            return {p: round(float(a) / duration_s, 3) for p, a in cur.fetchall()}
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", default="1,2,4")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--fixture", default="fixtures/bench_1080p.mp4")
    ap.add_argument("--duration", type=float, default=30.0)
    a = ap.parse_args()

    scales = [int(s) for s in a.scales.split(",")]
    fixture = open(a.fixture, "rb").read()
    all_jobs: list[str] = []
    rows = []

    for scale in scales:
        scale_workers(scale)
        for run in range(1, a.runs + 1):
            print(f"[scale={scale} run={run}] submitting {a.batch} jobs ...", flush=True)
            jids = submit_batch(fixture, a.batch)
            wait_terminal(jids)
            secs, done = batch_walltime(jids)
            jph = round(a.batch / secs * 3600, 1) if secs else 0
            rows.append((scale, run, round(secs, 1), done, jph))
            all_jobs += jids
            print(f"[scale={scale} run={run}] wall={secs:.1f}s done={done}/{a.batch} -> {jph} jobs/hr", flush=True)

    ratios = preset_ratios(all_jobs, a.duration)
    print("\n=== RESULTS ===")
    print(f"host: {_sh('nproc').strip()} cores | fixture {a.duration}s 1080p | batch {a.batch} | x264 {config.x264_preset}")
    print(f"{'scale':>6} {'run':>4} {'wall_s':>8} {'done':>5} {'jobs/hr':>9}")
    for scale, run, secs, done, jph in rows:
        print(f"{scale:>6} {run:>4} {secs:>8} {done:>5} {jph:>9}")
    print(f"\nper-preset encode ratio (encode_s / {a.duration}s): {ratios}")
    sigma_r = round(sum(ratios.values()), 3)
    print(f"Sigma_r = {sigma_r}")
    print(f"model jobs/hr @1 worker (2 concurrency): ~{round(3600 / (a.duration * sigma_r) * 2, 1)}")


if __name__ == "__main__":
    sys.exit(main())
