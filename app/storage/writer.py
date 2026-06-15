import hashlib
from pathlib import Path
from app.api.errors import UploadTooLarge

async def stream_to_disk(chunks, dest: Path, max_bytes: int) -> tuple[str, int]:
    """One pass: hash + count + write. Enforces max_bytes mid-stream; cleans the partial on
    over-limit. Returns (sha256_hex, total_bytes)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    total = 0
    try:
        with open(dest, "wb") as f:
            async for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLarge()
                sha.update(chunk)
                f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return sha.hexdigest(), total