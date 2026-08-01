from collections.abc import Mapping

from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    job_id: str | None = None
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        job_id: str | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.job_id, self.retryable = job_id, retryable


class InvalidUpload(ApiError):
    def __init__(self, message: str):
        super().__init__(422, "INVALID_UPLOAD", message)


class UnsupportedMedia(ApiError):
    def __init__(self, message: str):
        super().__init__(415, "UNSUPPORTED_MEDIA", message)


class UploadTooLarge(ApiError):
    def __init__(self):
        super().__init__(413, "UPLOAD_TOO_LARGE", "file exceeds the size limit")


class StoragePressure(ApiError):
    def __init__(self, job_id: str | None = None):
        super().__init__(
            503,
            "STORAGE_PRESSURE",
            "storage is full, please try again later",
            job_id=job_id,
            retryable=True,
        )


def error_response(
    status: int,
    code: str,
    message: str,
    job_id: str | None = None,
    retryable: bool = False,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            job_id=job_id,
            retryable=retryable,
        )
    )
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)
