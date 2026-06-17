import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import artifacts as art_route
from app.domain.playlist import Variant, build_master


# ---------- test infrastructure ----------

class FakeRedis:
    def __init__(self, status: str | None = "done"):
        self._status = status

    async def hget(self, key, field):
        if field == "status":
            return self._status
        return None


def _client(monkeypatch, status="done", tmp_path=None):
    """Return a TestClient with Redis stubbed to the given status.
    If tmp_path is provided, output_dir() is pointed there."""
    fake = FakeRedis(status)
    monkeypatch.setattr(art_route, "get_client", lambda: fake)
    if tmp_path is not None:
        monkeypatch.setattr(art_route.paths, "output_dir", lambda _jid: tmp_path)
    return TestClient(app, raise_server_exceptions=False)


def _seed_job(tmp_path, presets=("720p",), seg_count=2):
    """Create the file layout a done job would have."""
    master = build_master([Variant(p, 1_000_000, 1280, 720, "avc1.42001f,mp4a.40.2")
                           for p in presets])
    (tmp_path / "master.m3u8").write_text(master)
    (tmp_path / "web.mp4").write_bytes(b"fake-mp4")
    (tmp_path / "poster.jpg").write_bytes(b"fake-jpeg")
    (tmp_path / "sprite.jpg").write_bytes(b"fake-jpeg")
    (tmp_path / "embed.html").write_text("<video></video>")
    for p in presets:
        (tmp_path / p).mkdir()
        lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:4",
                 "#EXT-X-PLAYLIST-TYPE:VOD"]
        for i in range(seg_count):
            lines += [f"#EXTINF:4.000000,", f"seg_{i:05d}.ts"]
        lines.append("#EXT-X-ENDLIST")
        (tmp_path / p / "index.m3u8").write_text("\n".join(lines))
        for i in range(seg_count):
            (tmp_path / p / f"seg_{i:05d}.ts").write_bytes(b"fake-ts")


# ---------- state guard ----------

@pytest.mark.parametrize("status,expected", [
    ("queued",      404),
    ("transcoding", 404),
    (None,          404),
    ("expired",     410),
])
def test_guard_rejects_non_done(monkeypatch, status, expected):
    c = _client(monkeypatch, status=status)
    assert c.get("/jobs/j1/playlist").status_code == expected


# ---------- poster/sprite serve before done (written by the thumbs task mid-chord) ----------

@pytest.mark.parametrize("path", ["poster", "sprite"])
def test_thumb_served_during_transcode(monkeypatch, tmp_path, path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, status="transcoding", tmp_path=tmp_path)
    r = c.get(f"/jobs/j1/{path}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")


@pytest.mark.parametrize("path", ["poster", "sprite"])
def test_thumb_404_before_generated(monkeypatch, tmp_path, path):
    # transcoding, but the thumbs task hasn't written the file yet
    c = _client(monkeypatch, status="transcoding", tmp_path=tmp_path)
    assert c.get(f"/jobs/j1/{path}").status_code == 404


@pytest.mark.parametrize("path", ["poster", "sprite"])
def test_thumb_410_when_expired(monkeypatch, tmp_path, path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, status="expired", tmp_path=tmp_path)
    assert c.get(f"/jobs/j1/{path}").status_code == 410


# ---------- MIME + status for every endpoint ----------

def test_master_playlist_mime(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    r = c.get("/jobs/j1/playlist")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert r.headers["cache-control"] == "no-cache"


def test_rendition_playlist_mime(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    r = c.get("/jobs/j1/playlist/720p")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")


def test_segment_mime(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    r = c.get("/jobs/j1/segments/720p/seg_00000.ts")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("video/mp2t")
    assert "immutable" in r.headers["cache-control"]


def test_file_mime(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    r = c.get("/jobs/j1/file")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("video/mp4")


def test_poster_mime(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    assert c.get("/jobs/j1/poster").headers["content-type"].startswith("image/jpeg")


def test_sprite_mime(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    assert c.get("/jobs/j1/sprite").headers["content-type"].startswith("image/jpeg")


def test_player_mime(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    r = c.get("/jobs/j1/player")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


# ---------- URL-scheme contract ----------

def test_master_references_playlist_urls(monkeypatch, tmp_path):
    _seed_job(tmp_path, presets=("1080p", "720p"))
    c = _client(monkeypatch, tmp_path=tmp_path)
    body = c.get("/jobs/j1/playlist").text
    assert "playlist/1080p" in body          # generator writes playlist/{preset}
    assert "playlist/720p" in body
    assert "index.m3u8" not in body          # never the old FFmpeg-relative form


def test_rendition_rewrites_segment_refs(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    body = c.get("/jobs/j1/playlist/720p").text
    # handler rewrites bare seg_XXXXX.ts -> ../../segments/{preset}/seg_XXXXX.ts
    assert "../segments/720p/seg_00000.ts" in body
    assert body.count("seg_00000.ts") == body.count("../segments/720p/seg_00000.ts")


# ---------- traversal probes ----------

@pytest.mark.parametrize("preset", [
    "../etc",                # single path segment with ../ — rejected by allow-list before fs access
    "%2e%2e%2fetc",          # URL-encoded ../etc — httpx decodes before routing; allow-list rejects
    "a" * 200,               # long string not in catalog
    "4320p",                 # plausible-looking but not in catalog
])
def test_preset_traversal_rejected(monkeypatch, tmp_path, preset):
    # Note: probes with embedded slashes (../../etc) are split by FastAPI's router before reaching
    # our handler — the router itself is a structural defense against slash-based traversal.
    # We test single-segment traversal strings and URL-encoded variants, which DO reach the handler.
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    assert c.get(f"/jobs/j1/playlist/{preset}").status_code in (400, 404, 422)


@pytest.mark.parametrize("filename", [
    "seg_999999.ts",          # 6 digits not 5 — fails regex
    "index.m3u8",             # wrong extension
    "seg_00000.ts.evil",      # extra suffix
    "%2e%2e%2fetc",           # URL-encoded path traversal — fails regex
])
def test_segment_filename_rejected(monkeypatch, tmp_path, filename):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    assert c.get(f"/jobs/j1/segments/720p/{filename}").status_code in (400, 404, 422)


def test_unknown_preset_on_segment_is_404(monkeypatch, tmp_path):
    _seed_job(tmp_path)
    c = _client(monkeypatch, tmp_path=tmp_path)
    assert c.get("/jobs/j1/segments/4k/seg_00000.ts").status_code == 404
