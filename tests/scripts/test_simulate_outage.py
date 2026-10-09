"""CLI behaviour of scripts/simulate_outage.py (AUDIT F-33)."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from scripts import simulate_outage

REAL_CLIENT = httpx.Client


def use_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    """Route the script's httpx.Client through a mock handler; return the requests seen."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def client(**kwargs: Any) -> httpx.Client:
        return REAL_CLIENT(transport=httpx.MockTransport(record), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return seen


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        simulate_outage.parse_args(["--action", "trigerr"])
    assert exc.value.code == 2


def test_success_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = use_transport(
        monkeypatch,
        lambda _req: httpx.Response(200, json={"effective_success_rate": 0.0}),
    )
    assert simulate_outage.main(["--action", "trigger"]) == 0
    assert len(seen) == 1
    assert seen[0].url.path == "/acquirers/acquirer_alpha/admin/outage"


def test_http_error_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    use_transport(monkeypatch, lambda _req: httpx.Response(404, json={"detail": "Unknown"}))
    assert simulate_outage.main(["--action", "trigger", "--acquirer-id", "nope"]) == 1


def test_pulse_stops_after_failed_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = use_transport(monkeypatch, lambda _req: httpx.Response(503, text="down"))
    assert simulate_outage.main(["--action", "pulse", "--duration", "0"]) == 1
    assert len(seen) == 1


def test_unreachable_simulator_exits_non_zero() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    url = f"http://127.0.0.1:{port}"
    assert simulate_outage.main(["--action", "clear", "--acquirer-url", url]) == 1
