import pytest
from fastapi.testclient import TestClient

from app.api.errors import (
    ApiError,
    InspectionUnavailable,
    InvalidUpload,
    StoragePressure,
    UnsupportedMedia,
    UploadTooLarge,
)
from app.api.main import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "error,status,code,message,job_id,retryable",
    [
        (InvalidUpload("bad upload"), 422, "INVALID_UPLOAD", "bad upload", None, False),
        (UnsupportedMedia("bad format"), 415, "UNSUPPORTED_MEDIA", "bad format", None, False),
        (UploadTooLarge(), 413, "UPLOAD_TOO_LARGE", "file exceeds the size limit", None, False),
        (
            StoragePressure("j1"),
            503,
            "STORAGE_PRESSURE",
            "storage is full, please try again later",
            "j1",
            True,
        ),
        (
            InspectionUnavailable("j2"),
            503,
            "INSPECTION_UNAVAILABLE",
            "inspection is temporarily unavailable",
            "j2",
            True,
        ),
    ],
)
def test_named_errors(error, status, code, message, job_id, retryable):
    assert error.status == status
    assert error.code == code
    assert error.message == message
    assert error.job_id == job_id
    assert error.retryable is retryable
    assert str(error) == message


def test_api_error_string_is_public_message():
    error = ApiError(409, "WRONG_STATE", "job is already done", "j1")

    assert str(error) == "job is already done"


@pytest.mark.parametrize(
    "method,path,kwargs,status,code,message,job_id",
    [
        (
            "GET",
            "/jobs?limit=banana",
            {},
            422,
            "VALIDATION_ERROR",
            "request validation failed",
            None,
        ),
        (
            "POST",
            "/jobs/j1/transcode",
            {"json": {}},
            422,
            "VALIDATION_ERROR",
            "request validation failed",
            "j1",
        ),
        ("GET", "/missing-route", {}, 404, "ROUTE_NOT_FOUND", "Not Found", None),
        ("POST", "/status", {}, 405, "METHOD_NOT_ALLOWED", "Method Not Allowed", None),
    ],
)
def test_framework_errors_use_api_envelope(
    client, method, path, kwargs, status, code, message, job_id
):
    response = client.request(method, path, **kwargs)

    assert response.status_code == status
    assert response.json() == {
        "error": {
            "code": code,
            "message": message,
            "job_id": job_id,
            "retryable": False,
        }
    }


def test_method_error_preserves_allow_header(client):
    response = client.post("/status")

    assert response.headers["allow"] == "GET"


@pytest.mark.parametrize(
    "path,method,codes",
    [
        ("/upload", "post", ("413", "415", "422", "503")),
        ("/jobs/{job_id}/transcode", "post", ("404", "409", "410", "422", "503")),
        ("/jobs/{job_id}/playlist", "get", ("403", "404", "410", "503")),
    ],
)
def test_openapi_documents_error_envelope(path, method, codes):
    responses = app.openapi()["paths"][path][method]["responses"]

    for code in codes:
        schema = responses[code]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorResponse"}
