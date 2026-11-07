"""Tests for send endpoint rate limiting."""

import os
import sys
from unittest.mock import MagicMock, patch

import azure.functions as func
import pytest
from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError

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

    with patch.dict(os.environ, {"AzureWebJobsStorage__accountName": "", "AzureWebJobsStorage": ""}), patch(
        "function_app.time.monotonic", return_value=100
    ):
        assert function_app.send(_send_request("caller-key")).status_code == 400
        assert function_app.send(_send_request("caller-key")).status_code == 400
        response = function_app.send(_send_request("caller-key"))

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


def test_send_rate_limit_is_scoped_to_function_key(monkeypatch):
    monkeypatch.setattr(function_app, "SEND_RATE_LIMIT", 1)

    with patch.dict(os.environ, {"AzureWebJobsStorage__accountName": "", "AzureWebJobsStorage": ""}), patch(
        "function_app.time.monotonic", return_value=100
    ):
        assert function_app.send(_send_request("first-key")).status_code == 400
        assert function_app.send(_send_request("second-key")).status_code == 400
        assert function_app.send(_send_request("first-key")).status_code == 429


def test_send_rejects_function_key_in_multiple_locations():
    request = func.HttpRequest(
        method="POST",
        url="http://localhost/api/send",
        params={"code": "query-key"},
        headers={"x-functions-key": "header-key"},
        body=b"not-json",
    )

    response = function_app.send(request)

    assert response.status_code == 400


def test_shared_rate_limit_retries_after_concurrent_blob_update(monkeypatch):
    monkeypatch.setattr(function_app, "SEND_RATE_LIMIT", 2)
    state = {"value": None, "etag": None, "conflict": False}
    blob = MagicMock()

    def download_blob():
        if state["value"] is None:
            raise ResourceNotFoundError("missing")
        downloader = MagicMock()
        downloader.properties = {"etag": state["etag"]}
        downloader.readall.return_value = state["value"]
        return downloader

    def upload_blob(data, overwrite, **kwargs):
        if not overwrite and state["value"] is not None:
            raise ResourceModifiedError("already created")
        if overwrite and state["conflict"]:
            state["conflict"] = False
            state["value"] = b'{"timestamps":[100.1,100.1]}'
            state["etag"] = '"2"'
            raise ResourceModifiedError("concurrent update")
        if overwrite:
            assert kwargs["etag"] == state["etag"]
        state["value"] = data.encode("utf-8") if isinstance(data, str) else data
        state["etag"] = '"1"' if state["etag"] is None else '"2"'

    blob.download_blob.side_effect = download_blob
    blob.upload_blob.side_effect = upload_blob
    container = MagicMock()
    service = MagicMock()
    service.get_container_client.return_value = container
    service.get_blob_client.return_value = blob

    with patch.dict(os.environ, {"AzureWebJobsStorage__accountName": "stcouriertest"}), patch(
        "function_app._blob_service", return_value=service
    ), patch("function_app.time.time", return_value=100.1):
        assert function_app.send(_send_request("caller-key")).status_code == 400
        state["conflict"] = True
        response = function_app.send(_send_request("caller-key"))

    assert response.status_code == 429
    blob.upload_blob.assert_called()
