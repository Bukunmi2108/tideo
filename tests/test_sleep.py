import httpx

from app.core import sleep

IDLE = {
    "jobs": {"inspecting": 0, "queued": 0, "transcoding": 0, "awaiting_choice": 2},
    "queues": {name: 0 for name in sleep.QUEUE_NAMES},
    "kafka_lag": {"dispatcher": 0, "audit": 0},
}


def test_awaiting_choice_does_not_hold_lease():
    assert sleep.should_hold_lease(IDLE) is False


def test_work_or_unknown_state_holds_lease():
    queued = {**IDLE, "jobs": {**IDLE["jobs"], "queued": 1}}
    unavailable = {**IDLE, "queues": "unreachable"}

    assert sleep.should_hold_lease(queued) is True
    assert sleep.should_hold_lease(unavailable) is True


def test_refresh_calls_caddy_only_while_busy():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/status":
            return httpx.Response(200, json=IDLE)
        return httpx.Response(200, json={"ready": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert sleep.refresh(client, "tideo-api.duckdns.org") is False

    assert [request.url.path for request in calls] == ["/status"]

    calls.clear()
    busy = {**IDLE, "kafka_lag": {"dispatcher": 1, "audit": 0}}

    def busy_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=busy if request.url.path == "/status" else {"ready": True}
        )

    with httpx.Client(transport=httpx.MockTransport(busy_handler)) as client:
        assert sleep.refresh(client, "tideo-api.duckdns.org") is True

    assert [request.url.path for request in calls] == ["/status", "/readyz"]
    assert calls[-1].url == httpx.URL("https://tideo-api.duckdns.org/readyz")


def test_status_failure_refreshes_lease():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503 if request.url.path == "/status" else 200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert sleep.refresh(client, "tideo-api.duckdns.org") is True

    assert [request.url.path for request in calls] == ["/status", "/readyz"]
