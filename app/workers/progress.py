import time

def parse_progress_blocks(lines):
    """Yield one dict per -progress block (terminated by a `progress=` line)."""
    block = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        key, _, val = line.partition("=")
        block[key] = val
        if key == "progress":
            yield block
            block = {}

def out_time_seconds(block: dict) -> float:
    for k in ("out_time_us", "out_time_ms"):
        v = block.get(k)
        if v and v.lstrip("-").isdigit():
            return max(0.0, int(v) / 1_000_000)
    hms = block.get("out_time", "")
    try:
        h, m, s = hms.split(":")
        return max(0.0, int(h) * 3600 + int(m) * 60 + float(s))
    except ValueError:
        return 0.0

def percent(block: dict, duration_s: float) -> float:
    if not duration_s:
        return 0.0
    return max(0.0, min(out_time_seconds(block) / duration_s * 100.0, 99.5))

class Throttle:
    """Emit on >=1% delta OR >=2s elapsed, whichever first. clock injected for tests."""
    def __init__(self, min_delta=1.0, min_interval=2.0, clock=time.monotonic):
        self._min_delta, self._min_interval, self._clock = min_delta, min_interval, clock
        self._last_pct = -1.0
        self._last_t = clock() - min_interval

    def should_emit(self, pct: float) -> bool:
        now = self._clock()
        if pct - self._last_pct >= self._min_delta or now - self._last_t >= self._min_interval:
            self._last_pct, self._last_t = pct, now
            return True
        return False