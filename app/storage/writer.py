import hashlib
from functools import partial
from pathlib import Path

from anyio import open_file
from anyio.to_thread import run_sync

FLUSH_BYTES = 1 << 20


class UploadLimitExceeded(Exception):
    pass


async def stream_to_disk(chunks, dest: Path, max_bytes: int) -> tuple[str, int]:
    """Hash and store an upload in one pass."""
    await run_sync(partial(dest.parent.mkdir, parents=True, exist_ok=True))
    sha = hashlib.sha256()
    total = 0
    buf = bytearray()

    try:
        async with await open_file(dest, "wb") as f:
            async for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:
                    raise UploadLimitExceeded()
                buf += chunk
                if len(buf) >= FLUSH_BYTES:
                    sha.update(buf)
                    await f.write(buf)
                    buf.clear()
            if buf:
                sha.update(buf)
                await f.write(buf)
    except BaseException:
        await run_sync(partial(dest.unlink, missing_ok=True))
        raise
    return sha.hexdigest(), total
