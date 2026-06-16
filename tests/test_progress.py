from app.workers.progress import (
    Throttle,
    out_time_seconds,
    parse_progress_blocks,
    percent,
)

# A captured two-block -progress stream (the real shape FFmpeg emits).
SAMPLE = """\
frame=30
fps=30.0
out_time_us=1000000
out_time_ms=1000000
out_time=00:00:01.000000
speed=2.0x
progress=continue
frame=120
fps=30.0
out_time_us=4000000
out_time_ms=4000000
out_time=00:00:04.000000
speed=2.1x
progress=end
"""


def test_parse_yields_one_dict_per_block():
    blocks = list(parse_progress_blocks(SAMPLE.splitlines()))
    assert len(blocks) == 2
    assert blocks[0]["progress"] == "continue"
    assert blocks[1]["progress"] == "end"
    assert blocks[1]["out_time"] == "00:00:04.000000"


def test_out_time_ms_is_microseconds_not_milliseconds():
    # the FFmpeg trap: out_time_ms=4000000 is 4 seconds (microseconds), not 4000s
    assert out_time_seconds({"out_time_ms": "4000000"}) == 4.0
    assert out_time_seconds({"out_time_us": "4000000"}) == 4.0


def test_out_time_string_fallback():
    assert out_time_seconds({"out_time": "00:01:02.500000"}) == 62.5


def test_out_time_garbage_is_zero():
    assert out_time_seconds({}) == 0.0
    assert out_time_seconds({"out_time": "N/A"}) == 0.0


def test_percent_basic():
    assert percent({"out_time_us": "15000000"}, 30.0) == 50.0


def test_percent_zero_duration_is_zero():
    assert percent({"out_time_us": "4000000"}, 0) == 0.0


def test_percent_clamps_vfr_overshoot_to_995():
    # VFR sources can report out_time PAST duration -> must clamp, never exceed 99.5 while running
    assert percent({"out_time_us": "33000000"}, 30.0) == 99.5


# ---------- Throttle (fake clock) ----------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_throttle_emits_on_one_percent_delta():
    clk = Clock()
    th = Throttle(clock=clk)
    assert th.should_emit(0.0) is True       # first tick always
    assert th.should_emit(0.5) is False      # <1% and <2s
    assert th.should_emit(1.0) is True       # >=1% delta


def test_throttle_emits_on_time_even_without_delta():
    clk = Clock()
    th = Throttle(clock=clk)
    th.should_emit(10.0)                     # prime
    clk.t = 1.0
    assert th.should_emit(10.1) is False     # <1% and <2s
    clk.t = 2.5
    assert th.should_emit(10.1) is True      # >=2s elapsed


def test_throttle_suppresses_a_burst():
    clk = Clock()
    th = Throttle(clock=clk)
    emits = sum(th.should_emit(p) for p in [0.0, 0.1, 0.2, 0.3, 0.4])  # all within 1% and 0s
    assert emits == 1                        # only the first
