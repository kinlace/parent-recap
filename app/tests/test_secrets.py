"""Reading, storing and deleting secrets through `family_brief.system.secrets` (ADR 0020).

The outside edge is faked: the Keychain's `/usr/bin/security`, which the harness gives."""
from __future__ import annotations

import pytest

from family_brief.system import secrets


def test_a_stored_secret_reads_back_and_is_gone_once_deleted(harness):
    secrets.put("gateway-key", "sk-bf-key")
    assert secrets.get("gateway-key") == "sk-bf-key"

    secrets.delete("gateway-key")

    assert secrets.get("gateway-key") is None and harness.keychain == {}


def test_deleting_a_secret_that_is_not_there_is_fine(harness):
    secrets.delete("gateway-key")

    assert harness.keychain == {}


def test_a_secret_that_asks_before_it_is_read_needs_a_prompt(harness):
    harness.keychain["gateway-key"] = "sk-bf-key"
    harness.keychain_asks.add("gateway-key")

    with pytest.raises(secrets.SecretsError) as e:
        secrets.get("gateway-key")

    assert e.value.reason == "needs-prompt"
    assert secrets.has("gateway-key")  # without reading it, so nothing asks


@pytest.mark.parametrize("code, reason", [(-25308, "not-reachable"), (-128, "failed")])
def test_a_store_that_refuses_says_why_with_its_code(harness, code, reason):
    harness.keychain_refuses = code

    with pytest.raises(secrets.SecretsError) as e:
        secrets.put("gateway-key", "sk-bf-key")

    assert (e.value.reason, e.value.code) == (reason, code)
    assert harness.keychain == {}
