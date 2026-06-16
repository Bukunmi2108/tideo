from app.workers.tasks.thumbs import _poster_argv, sprite_plan


# ---------- sprite_plan grid math (pure) ----------

def test_long_source_is_full_10x10():
    p = sprite_plan(duration=300.0, fps=30.0)        # 9000 frames
    assert (p.tiles, p.cols, p.rows) == (100, 10, 10)
    assert p.sample_fps == "100/300.0"               # exact fraction for the fps filter


def test_short_source_shrinks_the_grid_no_padding():
    # 2s @ 30fps = 60 frames -> 60 tiles, 10x6 (never a half-empty 10x10)
    p = sprite_plan(duration=2.0, fps=30.0)
    assert (p.tiles, p.cols, p.rows) == (60, 10, 6)


def test_very_short_source_single_row():
    # 0.5s @ 10fps = 5 frames -> 5 tiles in one row
    p = sprite_plan(duration=0.5, fps=10.0)
    assert (p.tiles, p.cols, p.rows) == (5, 5, 1)


def test_grid_always_covers_every_tile():
    # rows*cols must be >= tiles (no tile dropped) and rows minimal (no fully-empty row)
    for dur, fps in [(300, 30), (2, 30), (0.5, 10), (10, 60), (7, 24)]:
        p = sprite_plan(dur, fps)
        assert p.cols * p.rows >= p.tiles
        assert (p.rows - 1) * p.cols < p.tiles


def test_missing_fps_falls_back():
    p = sprite_plan(duration=10.0, fps=0)            # fps unknown -> assume 30
    assert p.tiles == 100


# ---------- poster ----------

def test_poster_seeks_to_ten_percent():
    argv = _poster_argv("in.mp4", 100.0, "/o/poster.jpg")
    assert argv[argv.index("-ss") + 1] == "10.000"   # 10% of 100s
    assert "scale=1280:-2" in argv                   # aspect-preserving, 1280 wide
