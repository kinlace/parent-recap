"""A release zip verifies against its signature with either key the program ships, through the
system's ssh-keygen (ADR 0047). Each test makes its own keys; no private key is in the repo."""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from family_brief import release_signature
from family_brief.release_signature import NAMESPACE, SIGNERS, verified

pytestmark = pytest.mark.posix

# The two public keys of #913, the workflow's and the backup.
WORKFLOW_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMlgmC+GM8Ibijhy11C2GbmAI1+XZ2fafcGMDPOHEDWw"
BACKUP_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILRVwI3p+XA3qeq4r+KPrcrzhDR/Vpa9Q874aEcX9n5U"


def make_key(folder: Path, name: str) -> Path:
    key = folder / name
    subprocess.run([release_signature.SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "",
                    "-f", str(key)], check=True)
    return key


def public(key: Path) -> str:
    return key.with_suffix(".pub").read_text().strip()


def sign(key: Path, data: Path, namespace: str = NAMESPACE) -> Path:
    subprocess.run([release_signature.SSH_KEYGEN, "-Y", "sign", "-f", str(key), "-n", namespace,
                    str(data)], check=True, capture_output=True)
    return data.with_name(data.name + ".sig")


@pytest.fixture
def keys(tmp_path: Path) -> dict[str, Path]:
    folder = tmp_path / "keys"
    folder.mkdir()
    made = {name: make_key(folder, name) for name in ("workflow", "backup", "other")}
    signers = tmp_path / "release_signers"
    signers.write_text(
        "# Like the shipped file.\n"
        + "".join(f'{name} namespaces="{NAMESPACE}" {public(made[name])}\n'
                  for name in ("workflow", "backup")))
    return {**made, "signers": signers}


@pytest.fixture
def release_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "parent-recap-1.2.3.zip"
    zip_path.write_bytes(b"PK\x03\x04 a release" * 1000)
    return zip_path


@pytest.mark.parametrize("signer", ["workflow", "backup"])
def test_a_zip_signed_by_either_shipped_key_verifies(keys, release_zip, signer):
    signature = sign(keys[signer], release_zip)

    assert verified(release_zip, signature, signers=keys["signers"])


def test_a_changed_zip_fails(keys, release_zip):
    signature = sign(keys["workflow"], release_zip)
    release_zip.write_bytes(release_zip.read_bytes() + b"x")

    assert not verified(release_zip, signature, signers=keys["signers"])


def test_a_changed_signature_fails(keys, release_zip):
    signature = sign(keys["workflow"], release_zip)
    lines = signature.read_text().splitlines()
    # A letter in the middle of the armoured body, which holds the signature bytes.
    middle = len(lines) // 2
    lines[middle] = lines[middle][:10] + ("A" if lines[middle][10] != "A" else "B") + lines[middle][11:]
    signature.write_text("\n".join(lines) + "\n")

    assert not verified(release_zip, signature, signers=keys["signers"])


def test_a_signature_from_any_other_key_fails(keys, release_zip):
    signature = sign(keys["other"], release_zip)

    assert not verified(release_zip, signature, signers=keys["signers"])


def test_a_signature_made_under_another_namespace_fails(keys, release_zip):
    signature = sign(keys["workflow"], release_zip, namespace="file")

    assert not verified(release_zip, signature, signers=keys["signers"])


def test_a_missing_signature_fails(keys, release_zip, tmp_path):
    assert not verified(release_zip, tmp_path / "none.sig", signers=keys["signers"])


def test_a_signature_from_a_test_key_fails_against_the_shipped_keys(keys, release_zip):
    signature = sign(keys["workflow"], release_zip)

    assert not verified(release_zip, signature)


def test_the_shipped_keys_are_the_workflow_key_and_the_backup():
    lines = [line.split() for line in SIGNERS.read_text().splitlines()
             if line.strip() and not line.startswith("#")]

    assert {line[0]: " ".join(line[2:]) for line in lines} == {
        "workflow": WORKFLOW_KEY, "backup": BACKUP_KEY}
    assert all(line[1] == f'namespaces="{NAMESPACE}"' for line in lines)


def test_the_shipped_keys_are_installed_with_the_program():
    # An editable install finds the file in the tree either way; a real one only if it is listed.
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    assert "release_signers" in pyproject["tool"]["setuptools"]["package-data"]["family_brief"]
