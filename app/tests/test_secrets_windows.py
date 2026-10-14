"""The Windows adapter of `family_brief.system.secrets`: Credential Manager, one generic credential
per secret (ADR 0020, ADR 0021). Callers' tests use fake_system.FakeSecrets instead.

Nothing is faked: the secrets go into the Credential Manager of the Windows account running the
tests, under a name of each test's own (conftest's `credential_manager`), and are deleted after."""
from __future__ import annotations

import logging
import subprocess

import pytest

from conftest import REAL_RUN

from family_brief.system import secrets

pytestmark = pytest.mark.windows

NAME = "gateway-key"
SECRET = "sk-bf-fake-gateway-key-0123456789"


@pytest.fixture
def store(credential_manager) -> None:
    credential_manager.append(NAME)


def listed() -> str:
    """Every credential Windows' own `cmdkey` lists for this Windows user."""
    return REAL_RUN(["cmdkey", "/list"], capture_output=True, text=True).stdout


def test_a_stored_secret_is_read_back(store):
    secrets.put(NAME, SECRET)

    assert secrets.get(NAME) == SECRET
    assert secrets.has(NAME)


def test_each_secret_is_one_generic_credential_named_for_parent_recap(store):
    secrets.put(NAME, SECRET)

    shown = listed()
    assert secrets.where(NAME).startswith("Parent Recap") and secrets.where(NAME).endswith(NAME)
    assert secrets.where(NAME) in shown and "Generic" in shown
    assert SECRET not in shown
    assert secrets.STORE == "Credential Manager"


def test_storing_again_replaces_the_secret(store):
    secrets.put(NAME, "first-value")
    secrets.put(NAME, SECRET)

    assert secrets.get(NAME) == SECRET


def test_a_secret_that_was_never_stored_reads_as_none(store):
    assert secrets.get(NAME) is None
    assert not secrets.has(NAME)


def test_a_deleted_secret_is_gone_and_deleting_it_again_is_fine(store):
    secrets.put(NAME, SECRET)

    secrets.delete(NAME)
    secrets.delete(NAME)

    assert secrets.get(NAME) is None
    assert secrets.where(NAME) not in listed()


def test_a_secret_on_more_than_one_line_is_refused(store):
    with pytest.raises(ValueError):
        secrets.put(NAME, "one\ntwo")

    assert not secrets.has(NAME)


def test_the_store_can_be_written_from_here(store):
    assert secrets.reachable()


def test_a_secret_never_reaches_the_output_or_the_log_even_when_storing_it_fails(store, capsys,
                                                                                 caplog):
    caplog.set_level(logging.DEBUG)
    secrets.put(NAME, SECRET)
    secrets.get(NAME)
    too_long = SECRET * 200  # past what Credential Manager keeps for one credential

    with pytest.raises(secrets.SecretsError) as refused:
        secrets.put(NAME, too_long)

    printed = capsys.readouterr()
    assert SECRET not in printed.out + printed.err + caplog.text + str(refused.value)
    assert secrets.get(NAME) == SECRET  # the stored one is left as it was


def test_no_secret_goes_on_a_command_line(store, monkeypatch):
    started: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: started.append(cmd))

    secrets.put(NAME, SECRET)
    secrets.get(NAME)
    secrets.delete(NAME)

    assert started == []
