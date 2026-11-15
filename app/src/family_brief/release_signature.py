"""Whether a release zip is one of ours (ADR 0047).

The release workflow signs each release zip with `ssh-keygen -Y sign` under NAMESPACE and
publishes the signature beside it, as `<zip>.sig`. The program ships the two public keys a release
may be signed with, the workflow's and a backup a maintainer holds, in `release_signers`, and
checks a signature with the system's own `ssh-keygen`, by the full path its profile names: the one
macOS and Linux ship, and on Windows the OpenSSH client under System32 (#953). So verifying
installs nothing. A computer without it at that path, or with one too old to know `-Y` (before
OpenSSH 8.1), can't verify, which `NoSshKeygen` says apart from a signature that doesn't verify.
A signature says the zip is one of ours, not which release it is: the update reads the version
inside the verified zip.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .system import profile

# Parent Recap's own namespace: a signature one of the keys made for anything else, such as a
# git commit or another file, isn't a release's.
NAMESPACE = "release@parentrecap.com"
SIGNERS = Path(__file__).with_name("release_signers")


class NoSshKeygen(FileNotFoundError):
    """This computer has no ssh-keygen where its profile says, or one that can't verify a
    signature, so no release can be verified on it: not a signature that doesn't verify. An
    OSError, as any ssh-keygen that can't be run."""


def ssh_keygen() -> Path:
    """The ssh-keygen that checks a signature, by its full path, so a different ssh-keygen earlier
    on PATH can't answer for it."""
    return profile.current().ssh_keygen


def principals(signers: Path = SIGNERS) -> list[str]:
    """The names of the keys in an allowed signers file: `workflow` and `backup`."""
    return [line.split()[0] for line in signers.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def verified(zip_path: Path, signature: Path, signers: Path = SIGNERS) -> bool:
    """True when `signature` is a signature of `zip_path` under NAMESPACE by one of the keys in
    `signers`. False for a changed zip or signature, another key, another namespace or no
    signature file. Raises NoSshKeygen when this computer has no ssh-keygen at the profile's path
    or one without `-Y`, OSError when it can't be run at all, and subprocess.TimeoutExpired when it doesn't answer
    within two minutes."""
    program = ssh_keygen()
    if not program.is_file():
        raise NoSshKeygen(f"no ssh-keygen at {program}")
    if not signature.is_file():
        return False
    for principal in principals(signers):
        with zip_path.open("rb") as data:
            result = subprocess.run(
                [str(program), "-Y", "verify", "-f", str(signers), "-I", principal,
                 "-n", NAMESPACE, "-s", str(signature)],
                stdin=data, capture_output=True, timeout=120,
                # No console window when the update runs from pythonw.exe on Windows.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:
            return True
        # Its getopt's word, the same on Windows' OpenSSH 7.7, which Windows 10 1809 shipped.
        if b"unknown option -- Y" in result.stderr:
            raise NoSshKeygen(f"the ssh-keygen at {program} can't verify signatures")
    return False
