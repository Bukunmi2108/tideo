import hashlib
from pathlib import Path

from anyio.to_thread import run_sync

from app.api.errors import UploadTooLarge

FLUSH_BYTES = 1 << 20


async def stream_to_disk(chunks, dest: Path, max_bytes: int) -> tuple[str, int]:
    """One pass: hash + count + write off the event loop. Enforces max_bytes mid-stream and cleans the
    partial on over-limit. Returns (sha256_hex, total_bytes)."""
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
                if total > max_bytes:
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
