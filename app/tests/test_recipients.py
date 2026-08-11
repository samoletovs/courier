"""Tests for recipient allowlist management."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import azure.functions as func

# Ensure the app package is on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from function_app import (  # noqa: E402
    MAX_ADDRESS_LENGTH,
    EnvRecipientManager,
    _allowlist,
    _is_allowed,
    recipients,
    send,
)


class TestEnvRecipientManager:
    def test_parses_allowlist_from_env(self):
        with patch.dict(os.environ, {"ALLOWED_RECIPIENTS": "A@EXAMPLE.COM, @example.org, example.net,  "}, clear=False):
            manager = EnvRecipientManager.from_env()
        assert manager.allowlist_entries() == ["a@example.com", "@example.org", "example.net"]

    def test_allows_exact_email_domain_and_at_prefixed_domain(self):
        manager = EnvRecipientManager("allowed@example.com,example.net,@example.org")
        assert manager.is_allowed("allowed@example.com")
        assert manager.is_allowed("user@example.net")
        assert manager.is_allowed("member@example.org")

    def test_rejects_address_not_in_allowlist(self):
        manager = EnvRecipientManager("allowed@example.com,example.net")
        assert not manager.is_allowed("blocked@example.org")

    def test_exact_email_does_not_allow_other_user_same_domain(self):
        manager = EnvRecipientManager("allowed@example.com")
        assert not manager.is_allowed("other@example.com")

    def test_has_entries_true_when_allowlist_not_empty(self):
        assert EnvRecipientManager("allowed@example.com").has_entries()

    def test_has_entries_false_when_allowlist_empty(self):
        assert not EnvRecipientManager("").has_entries()


def test_allowlist_helper_uses_manager():
    with patch.dict(os.environ, {"ALLOWED_RECIPIENTS": "one@example.com,two@example.com"}, clear=False):
        assert _allowlist() == ["one@example.com", "two@example.com"]


def test_is_allowed_helper_rejects_malformed():
    assert not _is_allowed("not-an-email", ["example.com"])


def test_is_allowed_helper_accepts_bare_domain_entry():
    assert _is_allowed("person@example.com", ["example.com"])


def test_send_uses_recipient_manager_to_reject_disallowed_recipient():
    manager = MagicMock()
    manager.has_entries.return_value = True
    manager.is_allowed.return_value = False
    request = func.HttpRequest(
        method="POST",
        url="http://localhost/api/send",
        body=b'{"to":"blocked@example.com","subject":"Test","text":"Body"}',
    )

    with patch("function_app._recipient_manager", return_value=manager):
        response = send(request)

    assert response.status_code == 403
    manager.is_allowed.assert_called_once_with("blocked@example.com")


def _recipients_request(params: dict | None = None) -> func.HttpRequest:
    return func.HttpRequest(
        method="GET",
        url="http://localhost/api/recipients",
        body=b"",
        params=params or {},
    )


class TestRecipientsEndpoint:
    def test_lists_configured_allowlist(self):
        with patch.dict(os.environ, {"ALLOWED_RECIPIENTS": "one@example.com,@example.org"}, clear=False):
            response = recipients(_recipients_request())
        assert response.status_code == 200
        payload = json.loads(response.get_body())
        assert payload == {
            "configured": True,
            "count": 2,
            "entries": ["one@example.com", "@example.org"],
        }

    def test_reports_unconfigured_allowlist(self):
        with patch.dict(os.environ, {"ALLOWED_RECIPIENTS": ""}, clear=False):
            response = recipients(_recipients_request())
        payload = json.loads(response.get_body())
        assert payload == {"configured": False, "count": 0, "entries": []}

    def test_checks_allowed_address(self):
        with patch.dict(os.environ, {"ALLOWED_RECIPIENTS": "example.net"}, clear=False):
            response = recipients(_recipients_request({"address": "user@example.net"}))
        payload = json.loads(response.get_body())
        assert payload["check"] == {"address": "user@example.net", "allowed": True}

    def test_checks_disallowed_address(self):
        with patch.dict(os.environ, {"ALLOWED_RECIPIENTS": "example.net"}, clear=False):
            response = recipients(_recipients_request({"address": "user@example.com"}))
        payload = json.loads(response.get_body())
        assert payload["check"] == {"address": "user@example.com", "allowed": False}

    def test_rejects_oversized_address(self):
        domain = "@example.com"
        long_address = "a" * (MAX_ADDRESS_LENGTH + 1 - len(domain)) + domain
        with patch.dict(os.environ, {"ALLOWED_RECIPIENTS": "example.com"}, clear=False):
            response = recipients(_recipients_request({"address": long_address}))
        assert response.status_code == 400
