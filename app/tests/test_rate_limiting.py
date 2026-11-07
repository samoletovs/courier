"""Tests for send endpoint rate limiting."""

import os
import sys
from unittest.mock import patch

import azure.functions as func
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import function_app  # noqa: E402


@pytest.fixture(autouse=True)
def clear_rate_limit_state():
    with function_app._send_rate_limit_lock:
        function_app._send_rate_limit_requests.clear()
    yield
    with function_app._send_rate_limit_lock:
        function_app._send_rate_limit_requests.clear()


def _send_request(function_key: str) -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="http://localhost/api/send",
        params={"code": function_key},
        body=b"not-json",
    )


def test_send_returns_429_after_rate_limit(monkeypatch):
    monkeypatch.setattr(function_app, "SEND_RATE_LIMIT", 2)

    with patch("function_app.time.monotonic", return_value=100):
        assert function_app.send(_send_request("caller-key")).status_code == 400
        assert function_app.send(_send_request("caller-key")).status_code == 400
        response = function_app.send(_send_request("caller-key"))

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


def test_send_rate_limit_is_scoped_to_function_key(monkeypatch):
    monkeypatch.setattr(function_app, "SEND_RATE_LIMIT", 1)

    with patch("function_app.time.monotonic", return_value=100):
        assert function_app.send(_send_request("first-key")).status_code == 400
        assert function_app.send(_send_request("second-key")).status_code == 400
        assert function_app.send(_send_request("first-key")).status_code == 429
