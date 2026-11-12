"""Whether a release zip is one of ours (ADR 0047).

The release workflow signs each release zip with `ssh-keygen -Y sign` under NAMESPACE and
publishes the signature beside it, as `<zip>.sig`. The program ships the two public keys a release
may be signed with, the workflow's and a backup a maintainer holds, in `release_signers`, and
checks a signature with the `ssh-keygen` that macOS and Linux ship, so verifying needs nothing
installed. A signature says the zip is one of ours, not which release it is: the update reads
the version inside the verified zip.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# Parent Recap's own namespace: a signature one of the keys made for anything else, such as a
# git commit or another file, isn't a release's.
NAMESPACE = "release@parentrecap.com"
SIGNERS = Path(__file__).with_name("release_signers")
# By its full path, so a different ssh-keygen earlier on PATH can't answer for it.
SSH_KEYGEN = "/usr/bin/ssh-keygen"


def principals(signers: Path = SIGNERS) -> list[str]:
    """The names of the keys in an allowed signers file: `workflow` and `backup`."""
    return [line.split()[0] for line in signers.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def verified(zip_path: Path, signature: Path, signers: Path = SIGNERS) -> bool:
    """True when `signature` is a signature of `zip_path` under NAMESPACE by one of the keys in
    `signers`. False for a changed zip or signature, another key, another namespace or no
    signature file. Raises OSError when ssh-keygen can't be run at all, and
    subprocess.TimeoutExpired when it doesn't answer within two minutes."""
    if not signature.is_file():
        return False
    for principal in principals(signers):
        with zip_path.open("rb") as data:
            result = subprocess.run(
                [SSH_KEYGEN, "-Y", "verify", "-f", str(signers), "-I", principal,
                 "-n", NAMESPACE, "-s", str(signature)],
                stdin=data, capture_output=True, timeout=120)
        if result.returncode == 0:
            return True
    return False
