from pydantic import BaseModel

class JobResponse(BaseModel):
    job_id: str
    status: str
    source: dict | None = None
    recommended_presets: list[str] | None = None
    web_safe: bool | None = None
    web_safe_reason: str | None = None
    progress: dict | None = None
    results: dict | None = None
    error: dict | None = None