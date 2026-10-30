"""The Linux adapter of `family_brief.system.secrets`: one owner-only file per secret under the
Household's config folder (ADR 0030). Callers' tests use fake_system.FakeSecrets instead.

Nothing is faked: the files are written for real, in the test's temporary home."""
from __future__ import annotations

import getpass
import logging
import stat
import subprocess
from pathlib import Path

import pytest

from family_brief.system import secrets, secrets_linux

pytestmark = pytest.mark.posix

NAME = "gateway-key"
SECRET = "sk-bf-fake-gateway-key-0123456789"


@pytest.fixture
def store(home: Path, monkeypatch) -> Path:
    """The Linux store in place of the harness's fake; returns the folder it keeps secrets in."""
    monkeypatch.setattr(secrets, "_adapter", secrets_linux)
    return home / ".family" / "secrets"


def test_a_stored_secret_is_read_back(store):
    secrets.put(NAME, SECRET)

    assert secrets.get(NAME) == SECRET
    assert secrets.has(NAME)


def test_each_secret_is_a_file_only_its_owner_may_read_in_the_config_folder(store):
    secrets.put(NAME, SECRET)

    path = Path(secrets.where(NAME))
    assert path == store / NAME
    assert path.read_text(encoding="utf-8").strip() == SECRET
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700  # ~/.family, made here
    assert secrets.deleting_asks() == ""


def test_storing_again_replaces_the_secret_and_tightens_a_file_others_could_read(store):
    secrets.put(NAME, "first-value")
    (store / NAME).chmod(0o644)
    store.chmod(0o755)

    secrets.put(NAME, SECRET)

    assert secrets.get(NAME) == SECRET
    assert stat.S_IMODE((store / NAME).stat().st_mode) == 0o600
    assert stat.S_IMODE(store.stat().st_mode) == 0o700


def test_a_secret_that_was_never_stored_reads_as_none(store):
    assert secrets.get(NAME) is None
    assert not secrets.has(NAME)


def test_a_deleted_secret_is_gone_and_deleting_it_again_is_fine(store):
    secrets.put(NAME, SECRET)

    secrets.delete(NAME)
    secrets.delete(NAME)

    assert secrets.get(NAME) is None
    assert not (store / NAME).exists()


def test_a_secret_on_more_than_one_line_is_refused(store):
    with pytest.raises(ValueError):
        secrets.put(NAME, "one\ntwo")

    assert not secrets.has(NAME)


def test_a_name_that_would_leave_the_folder_is_refused(store):
    with pytest.raises(ValueError):
        secrets.put("../config.yaml", SECRET)


def test_the_store_can_be_written_from_here(store):
    assert secrets.reachable()


def test_a_secret_typed_at_the_hidden_prompt_is_stored(store, monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(getpass, "getpass", lambda text="": asked.append(text) or f" {SECRET} ")

    secrets.put_typed(NAME)

    assert secrets.get(NAME) == SECRET
    assert len(asked) == 1 and NAME in asked[0] and SECRET not in asked[0]


@pytest.mark.parametrize("typed", ["", KeyboardInterrupt, EOFError])
def test_nothing_typed_at_the_prompt_stores_nothing(store, monkeypatch, typed):
    def getpass_(text: str = "") -> str:
        if isinstance(typed, str):
            return typed
        raise typed()
    monkeypatch.setattr(getpass, "getpass", getpass_)

    with pytest.raises(secrets.SecretsError):
        secrets.put_typed(NAME)

    assert not secrets.has(NAME)


def test_a_store_that_cant_be_written_says_so_without_the_secret(store, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    store.parent.mkdir()
    store.write_text("not a folder")

    with pytest.raises(secrets.SecretsError) as refused:
        secrets.put(NAME, SECRET)

    printed = capsys.readouterr()
    assert refused.value.reason == "failed"
    assert SECRET not in printed.out + printed.err + caplog.text + str(refused.value)


def test_no_secret_goes_on_a_command_line(store, monkeypatch):
    started: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: started.append(cmd))

    secrets.put(NAME, SECRET)
    secrets.get(NAME)
    secrets.delete(NAME)

    assert started == []
