"""Tests for recipient allowlist management."""

import os
import sys
from unittest.mock import patch


# Ensure the app package is on the path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from function_app import EnvRecipientManager, _allowlist, _is_allowed  # noqa: E402


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
