import asyncio
import hashlib

import pytest

from app.api.errors import UploadTooLarge
from app.storage.writer import FLUSH_BYTES, stream_to_disk


async def gen(chunks):
    for c in chunks:
        yield c


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("sizes", [
    [],                                     # empty stream
    [b"hello"],                             # single small chunk
    [b"a" * 100, b"b" * 200, b"c" * 50],    # several sub-flush chunks
    [b"x" * (FLUSH_BYTES + 7), b"y" * 13],  # spans a flush boundary, unaligned tail
    [b"z"] * (FLUSH_BYTES + 5),             # many tiny chunks summing past a flush
])
def test_hash_and_bytes_match_reference(sizes, tmp_path):
    dest = tmp_path / "j" / "source.mp4"
    payload = b"".join(sizes)
    sha, total = run(stream_to_disk(gen(sizes), dest, max_bytes=1 << 30))
    assert total == len(payload)
    assert sha == hashlib.sha256(payload).hexdigest()
    assert dest.read_bytes() == payload


def test_over_limit_raises_and_cleans_partial(tmp_path):
    dest = tmp_path / "j" / "source.mp4"
    chunks = [b"a" * 600, b"b" * 600]       # 1200 total, limit 1000
    with pytest.raises(UploadTooLarge):
        run(stream_to_disk(gen(chunks), dest, max_bytes=1000))
    assert not dest.exists()                # partial removed


def test_iterator_error_cleans_partial_and_propagates(tmp_path):
    dest = tmp_path / "j" / "source.mp4"

    async def boom():
        yield b"a" * (FLUSH_BYTES + 1)      # forces a flush, file now non-empty on disk
        raise RuntimeError("stream died")

    with pytest.raises(RuntimeError, match="stream died"):
        run(stream_to_disk(boom(), dest, max_bytes=1 << 30))
    assert not dest.exists()
