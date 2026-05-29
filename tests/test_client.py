from __future__ import annotations

import base64
from typing import Any

import httpx

from paprika_mcp.client import DEFAULT_USER_AGENT, PaprikaClient


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
