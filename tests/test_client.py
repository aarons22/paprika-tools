from __future__ import annotations

import base64
import gzip
import json
from typing import Any

import pytest
import httpx

from paprika_mcp.client import DEFAULT_USER_AGENT, PaprikaClient, PaprikaRetryExhausted


def response(status_code: int = 200, json: Any | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=json,
        request=httpx.Request("GET", "https://www.paprikaapp.com/api/test"),
    )


def test_authenticated_requests_send_default_headers(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        return response(json={"result": {"recipes": 1}})

    monkeypatch.setattr(httpx, "request", fake_request)

    client = PaprikaClient(email="user@example.test", password="secret")
    client._token = "cached-token"

    assert client.get_sync_status() == {"recipes": 1}

    headers = httpx.Headers(captured["headers"])
    assert headers["Authorization"] == "Bearer cached-token"
    assert headers["User-Agent"] == DEFAULT_USER_AGENT
    assert headers["Accept-Encoding"] == "gzip, deflate"


def test_login_requests_send_default_headers(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        return response(json={"result": {"token": "new-token"}})

    monkeypatch.setattr(httpx, "post", fake_post)

    client = PaprikaClient(email="user@example.test", password="secret")

    assert client._authenticate() == "new-token"

    credentials = base64.b64encode(b"user@example.test:secret").decode()
    headers = httpx.Headers(captured["headers"])
    assert headers["Authorization"] == f"Basic {credentials}"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert headers["User-Agent"] == DEFAULT_USER_AGENT
    assert headers["Accept-Encoding"] == "gzip, deflate"


def test_constructor_header_overrides_are_used(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        captured["headers"] = kwargs["headers"]
        return response(json={"result": []})

    monkeypatch.setattr(httpx, "request", fake_request)

    client = PaprikaClient(
        email="user@example.test",
        password="secret",
        user_agent="custom-agent",
        default_headers={"Accept-Encoding": "identity", "X-Test": "1"},
    )
    client._token = "cached-token"

    client.list_recipes()

    headers = httpx.Headers(captured["headers"])
    assert headers["User-Agent"] == "custom-agent"
    assert headers["Accept-Encoding"] == "identity"
    assert headers["X-Test"] == "1"


def test_request_headers_override_defaults(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        captured["headers"] = kwargs["headers"]
        return response(json={"result": True})

    monkeypatch.setattr(httpx, "request", fake_request)

    client = PaprikaClient(email="user@example.test", password="secret")
    client._token = "cached-token"

    client._request("GET", "/v2/sync/status/", headers={"User-Agent": "per-call"})

    headers = httpx.Headers(captured["headers"])
    assert headers["Authorization"] == "Bearer cached-token"
    assert headers["User-Agent"] == "per-call"
    assert headers["Accept-Encoding"] == "gzip, deflate"


def test_503_retries_then_succeeds(monkeypatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return response(503, json={"error": "busy"})
        return response(json={"result": {"recipes": 1}})

    monkeypatch.setattr(httpx, "request", fake_request)

    client = PaprikaClient(
        email="user@example.test",
        password="secret",
        max_retries=2,
        retry_backoff_base=0.1,
        retry_jitter=0,
        sleep=sleeps.append,
    )
    client._token = "cached-token"

    assert client.get_sync_status() == {"recipes": 1}
    assert len(calls) == 2
    assert sleeps == [0.1]


def test_503_retry_budget_exhaustion(monkeypatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        calls.append(1)
        return response(503, json={"error": "busy"})

    monkeypatch.setattr(httpx, "request", fake_request)

    client = PaprikaClient(
        email="user@example.test",
        password="secret",
        max_retries=2,
        retry_backoff_base=0.1,
        retry_jitter=0,
        sleep=sleeps.append,
    )
    client._token = "cached-token"

    with pytest.raises(PaprikaRetryExhausted) as exc:
        client.get_sync_status()

    assert exc.value.status_code == 503
    assert exc.value.attempts == 3
    assert len(calls) == 3
    assert sleeps == [0.1, 0.2]


def test_transport_errors_retry_then_succeed(monkeypatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("temporary failure")
        return response(json={"result": {"recipes": 1}})

    monkeypatch.setattr(httpx, "request", fake_request)

    client = PaprikaClient(
        email="user@example.test",
        password="secret",
        max_retries=2,
        retry_backoff_base=0.1,
        retry_jitter=0,
        sleep=sleeps.append,
    )
    client._token = "cached-token"

    assert client.get_sync_status() == {"recipes": 1}
    assert len(calls) == 2
    assert sleeps == [0.1]


def test_transport_error_retry_budget_exhaustion(monkeypatch) -> None:
    calls: list[int] = []

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "request", fake_request)

    client = PaprikaClient(
        email="user@example.test",
        password="secret",
        max_retries=1,
        retry_backoff_base=0,
        retry_jitter=0,
        sleep=lambda _: None,
    )
    client._token = "cached-token"

    with pytest.raises(PaprikaRetryExhausted) as exc:
        client.get_sync_status()

    assert exc.value.status_code is None
    assert exc.value.attempts == 2
    assert isinstance(exc.value.last_error, httpx.ReadTimeout)
    assert len(calls) == 2


def test_create_grocery_item_sends_fresh_sync_hash(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs) -> httpx.Response:
        captured["files"] = kwargs["files"]
        return response(json={"result": True})

    monkeypatch.setattr(httpx, "request", fake_request)

    client = PaprikaClient(email="user@example.test", password="secret")
    client._token = "cached-token"

    assert client.create_grocery_item("list-1", "Milk") == {"result": True}

    payload = gzip.decompress(captured["files"]["data"][1])
    items = json.loads(payload)
    assert len(items) == 1
    assert items[0]["sync_hash"]
    assert len(items[0]["sync_hash"]) == 64
    assert items[0]["sync_hash"] == items[0]["sync_hash"].upper()
