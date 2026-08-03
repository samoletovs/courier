"""Tests for feedback endpoints and helper functions."""

import json
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest
import azure.functions as func

# Ensure the app package is on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from function_app import (  # noqa: E402
    _feedback_line,
    _append_feedback,
    _read_feedback,
    feedback,
    feedback_export,
    _PROJECT_RE,
    _FEEDBACK_VERDICTS,
    MAX_FEEDBACK_URL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_get(params: dict) -> func.HttpRequest:
    """Construct a GET HttpRequest with the given query parameters."""
    return func.HttpRequest(
        method="GET",
        url="http://localhost/api/feedback",
        params=params,
        body=b"",
    )


# ---------------------------------------------------------------------------
# _PROJECT_RE / _FEEDBACK_VERDICTS / MAX_FEEDBACK_URL constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_valid_project_names(self):
        for name in ("my-project", "dealscout", "a1", "abc-123-def"):
            assert _PROJECT_RE.match(name), f"{name!r} should be valid"

    def test_invalid_project_names(self):
        for name in ("", "-start", "A-uppercase", "has space", "a" * 33):
            assert not _PROJECT_RE.match(name), f"{name!r} should be invalid"

    def test_verdicts_are_up_and_down(self):
        assert set(_FEEDBACK_VERDICTS) == {"up", "down"}

    def test_max_feedback_url_is_sensible(self):
        assert MAX_FEEDBACK_URL == 2048


# ---------------------------------------------------------------------------
# _feedback_line
# ---------------------------------------------------------------------------

class TestFeedbackLine:
    def test_returns_newline_terminated_json(self):
        line = _feedback_line("proj", "up", "https://example.com")
        assert line.endswith("\n")
        data = json.loads(line)
        assert data["project"] == "proj"
        assert data["verdict"] == "up"
        assert data["url"] == "https://example.com"
        assert "ts" in data

    def test_timestamp_is_utc_iso8601(self):
        line = _feedback_line("proj", "down", "https://example.com/path")
        data = json.loads(line)
        # Must be parseable and contain timezone info (+00:00 or Z)
        ts = data["ts"]
        assert "+" in ts or ts.endswith("Z"), f"Expected UTC offset in {ts!r}"

    def test_down_verdict_stored(self):
        line = _feedback_line("x1", "down", "https://x.example.com")
        assert json.loads(line)["verdict"] == "down"


# ---------------------------------------------------------------------------
# _append_feedback (blob storage interactions mocked)
# ---------------------------------------------------------------------------

STORAGE_ENV = {"AzureWebJobsStorage__accountName": "stcouriertest"}


class TestAppendFeedback:
    def _make_mocks(self):
        blob_mock = MagicMock()
        blob_mock.exists.return_value = True
        container_mock = MagicMock()
        container_mock.get_blob_client.return_value = blob_mock
        service_mock = MagicMock()
        service_mock.get_container_client.return_value = container_mock
        return service_mock, container_mock, blob_mock

    @patch("function_app._blob_service")
    @patch.dict(os.environ, STORAGE_ENV)
    def test_appends_to_existing_blob(self, mock_blob_service):
        service_mock, container_mock, blob_mock = self._make_mocks()
        mock_blob_service.return_value = service_mock

        _append_feedback("myproject", "up", "https://example.com")

        container_mock.create_container.assert_called_once()
        blob_mock.append_block.assert_called_once()
        block_data = blob_mock.append_block.call_args[0][0]
        record = json.loads(block_data.decode("utf-8"))
        assert record["project"] == "myproject"
        assert record["verdict"] == "up"

    @patch("function_app._blob_service")
    @patch.dict(os.environ, STORAGE_ENV)
    def test_creates_blob_when_not_exists(self, mock_blob_service):
        service_mock, container_mock, blob_mock = self._make_mocks()
        blob_mock.exists.return_value = False
        mock_blob_service.return_value = service_mock

        _append_feedback("newproj", "down", "https://example.com")

        blob_mock.create_append_blob.assert_called_once()
        blob_mock.append_block.assert_called_once()

    @patch("function_app._blob_service")
    @patch.dict(os.environ, STORAGE_ENV)
    def test_container_exists_error_is_swallowed(self, mock_blob_service):
        from azure.core.exceptions import ResourceExistsError

        service_mock, container_mock, blob_mock = self._make_mocks()
        container_mock.create_container.side_effect = ResourceExistsError("exists")
        mock_blob_service.return_value = service_mock

        # Should not raise
        _append_feedback("proj", "up", "https://example.com")
        blob_mock.append_block.assert_called_once()

    @patch("function_app._blob_service")
    @patch.dict(os.environ, STORAGE_ENV)
    def test_blob_exists_race_condition_swallowed(self, mock_blob_service):
        from azure.core.exceptions import ResourceExistsError

        service_mock, container_mock, blob_mock = self._make_mocks()
        blob_mock.exists.return_value = False
        blob_mock.create_append_blob.side_effect = ResourceExistsError("race")
        mock_blob_service.return_value = service_mock

        _append_feedback("proj", "up", "https://example.com")
        blob_mock.append_block.assert_called_once()


# ---------------------------------------------------------------------------
# _read_feedback
# ---------------------------------------------------------------------------

class TestReadFeedback:
    @patch("function_app._blob_service")
    @patch.dict(os.environ, STORAGE_ENV)
    def test_returns_blob_content(self, mock_blob_service):
        jsonl = '{"project":"p1","verdict":"up","url":"https://x.com","ts":"2026-01-01T00:00:00+00:00"}\n'
        blob_mock = MagicMock()
        blob_mock.download_blob.return_value.readall.return_value = jsonl.encode()
        service_mock = MagicMock()
        service_mock.get_blob_client.return_value = blob_mock
        mock_blob_service.return_value = service_mock

        result = _read_feedback("p1")
        assert result == jsonl

    @patch("function_app._blob_service")
    @patch.dict(os.environ, STORAGE_ENV)
    def test_returns_empty_string_when_not_found(self, mock_blob_service):
        from azure.core.exceptions import ResourceNotFoundError

        blob_mock = MagicMock()
        blob_mock.download_blob.side_effect = ResourceNotFoundError("not found")
        service_mock = MagicMock()
        service_mock.get_blob_client.return_value = blob_mock
        mock_blob_service.return_value = service_mock

        result = _read_feedback("no-votes-yet")
        assert result == ""


# ---------------------------------------------------------------------------
# GET /api/feedback  (anonymous write endpoint)
# ---------------------------------------------------------------------------

VALID_FEEDBACK_PARAMS = {
    "p": "dealscout",
    "v": "up",
    "u": "https://newsletter.example.com/issue/42",
}


class TestFeedbackEndpoint:
    @patch("function_app._append_feedback")
    def test_valid_up_vote_returns_200(self, mock_append):
        req = _make_get(VALID_FEEDBACK_PARAMS)
        resp = feedback(req)
        assert resp.status_code == 200
        assert "👍" in resp.get_body().decode()
        mock_append.assert_called_once_with(
            "dealscout", "up", "https://newsletter.example.com/issue/42"
        )

    @patch("function_app._append_feedback")
    def test_valid_down_vote_returns_200(self, mock_append):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "v": "down"})
        resp = feedback(req)
        assert resp.status_code == 200
        assert "👎" in resp.get_body().decode()

    def test_missing_project_returns_400(self):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "p": ""})
        resp = feedback(req)
        assert resp.status_code == 400

    def test_invalid_project_returns_400(self):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "p": "-starts-with-dash"})
        resp = feedback(req)
        assert resp.status_code == 400

    def test_invalid_verdict_returns_400(self):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "v": "maybe"})
        resp = feedback(req)
        assert resp.status_code == 400

    def test_missing_url_returns_400(self):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "u": ""})
        resp = feedback(req)
        assert resp.status_code == 400

    def test_non_http_url_returns_400(self):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "u": "ftp://evil.com"})
        resp = feedback(req)
        assert resp.status_code == 400

    def test_oversized_url_returns_400(self):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "u": "https://" + "x" * 2048})
        resp = feedback(req)
        assert resp.status_code == 400

    @patch("function_app._append_feedback", side_effect=RuntimeError("boom"))
    def test_storage_error_returns_502(self, _mock):
        req = _make_get(VALID_FEEDBACK_PARAMS)
        resp = feedback(req)
        assert resp.status_code == 502

    @patch("function_app._append_feedback")
    def test_response_content_type_is_html(self, mock_append):
        req = _make_get(VALID_FEEDBACK_PARAMS)
        resp = feedback(req)
        assert "text/html" in (resp.mimetype or "")

    @patch("function_app._append_feedback")
    def test_project_is_lowercased(self, mock_append):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "p": "dealscout"})
        feedback(req)
        mock_append.assert_called_once()
        assert mock_append.call_args[0][0] == "dealscout"

    @patch("function_app._append_feedback")
    def test_url_with_https_prefix_accepted(self, mock_append):
        req = _make_get({**VALID_FEEDBACK_PARAMS, "u": "https://secure.example.com/page"})
        resp = feedback(req)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/feedback/export  (function-key read endpoint)
# ---------------------------------------------------------------------------

class TestFeedbackExportEndpoint:
    @patch("function_app._read_feedback")
    def test_valid_project_returns_jsonl(self, mock_read):
        jsonl = '{"project":"p1","verdict":"up","url":"https://x.com","ts":"2026-01-01T00:00:00+00:00"}\n'
        mock_read.return_value = jsonl

        req = func.HttpRequest(
            method="GET",
            url="http://localhost/api/feedback/export",
            params={"p": "p1"},
            body=b"",
        )
        resp = feedback_export(req)
        assert resp.status_code == 200
        assert resp.get_body().decode() == jsonl
        assert "ndjson" in (resp.mimetype or "")

    @patch("function_app._read_feedback")
    def test_no_votes_returns_empty_200(self, mock_read):
        mock_read.return_value = ""
        req = func.HttpRequest(
            method="GET",
            url="http://localhost/api/feedback/export",
            params={"p": "newproj"},
            body=b"",
        )
        resp = feedback_export(req)
        assert resp.status_code == 200
        assert resp.get_body() == b""

    def test_invalid_project_returns_400(self):
        req = func.HttpRequest(
            method="GET",
            url="http://localhost/api/feedback/export",
            params={"p": "INVALID_PROJECT"},
            body=b"",
        )
        resp = feedback_export(req)
        assert resp.status_code == 400

    def test_missing_project_returns_400(self):
        req = func.HttpRequest(
            method="GET",
            url="http://localhost/api/feedback/export",
            params={},
            body=b"",
        )
        resp = feedback_export(req)
        assert resp.status_code == 400

    @patch("function_app._read_feedback", side_effect=RuntimeError("blob error"))
    def test_storage_error_returns_502(self, _mock):
        req = func.HttpRequest(
            method="GET",
            url="http://localhost/api/feedback/export",
            params={"p": "p1"},
            body=b"",
        )
        resp = feedback_export(req)
        assert resp.status_code == 502
