import hashlib
from pathlib import Path

from anyio.to_thread import run_sync

from app.api.errors import UploadTooLarge

# Batch chunks to this size before handing the (blocking) hash+write to a worker thread, so a big
# upload doesn't stall other requests on the event loop. ~1 MB keeps the thread-hop count low while
# capping in-flight memory.
FLUSH_BYTES = 1 << 20


async def stream_to_disk(chunks, dest: Path, max_bytes: int) -> tuple[str, int]:
    """One pass: hash + count + write. Enforces max_bytes mid-stream; cleans the partial on
    over-limit. The hash/write run off the event loop (batched) so concurrent requests aren't
    blocked. Returns (sha256_hex, total_bytes)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    total = 0
    buf = bytearray()

    def flush(data: bytes, f) -> None:
        sha.update(data)
        f.write(data)

    try:
        with open(dest, "wb") as f:
            async for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:                  # cheap arithmetic gate, stays on the loop
                    raise UploadTooLarge()
                buf += chunk
                if len(buf) >= FLUSH_BYTES:
                    await run_sync(flush, bytes(buf), f)
                    buf.clear()
            if buf:
                await run_sync(flush, bytes(buf), f)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return sha.hexdigest(), total
