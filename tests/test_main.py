import asyncio

from fastapi.testclient import TestClient

from app.api import main as main_module

client = TestClient(main_module.app, raise_server_exceptions=False)


def test_public_surfaces():
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 404
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_allows_only_configured_browser_origins():
    allowed = main_module.config.allowed_origin_list[0]
    headers = {"access-control-request-method": "POST"}

    response = client.options("/upload", headers={**headers, "origin": allowed})
    rejected = client.options(
        "/upload", headers={**headers, "origin": "https://example.invalid"}
    )

    assert response.headers["access-control-allow-origin"] == allowed
    assert "access-control-allow-origin" not in rejected.headers


def test_readyz_names_failed_dependencies(monkeypatch):
    async def probe(host, _port):
        return host == "up"

    monkeypatch.setattr(
        main_module,
        "DEPENDENCIES",
        [("redis", "up", 1), ("kafka", "down", 2)],
    )
    monkeypatch.setattr(main_module, "_probe", probe)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"ready": False, "failing": ["kafka"]}


def test_probe_treats_connection_failure_as_not_ready(monkeypatch):
    async def fail(_host, _port):
        raise OSError("unreachable")

    monkeypatch.setattr(main_module.asyncio, "open_connection", fail)

    assert asyncio.run(main_module._probe("service", 1234)) is False
