from app.core.config import config


def _key(sha: str) -> str:
    return f"content:{sha}"

def _ttl() -> int:
    return config.output_ttl_days * 86400

async def claim(r, sha: str, job_id: str) -> bool:
    return bool(await r.set(_key(sha), job_id, nx=True, ex=_ttl()))

async def owner(r, sha: str) -> str | None:
    return await r.get(_key(sha))

async def is_valid(r, job_id: str) -> bool:
    record = await r.hgetall(f"job:{job_id}")
    if not record:
        return False
    if record.get("status") == "failed":
        return False
    return True

async def reclaim(r, sha: str, job_id: str) -> None:
    await r.set(_key(sha), job_id, ex=_ttl())