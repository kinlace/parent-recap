#!/usr/bin/env python3
"""Store the long-lived Claude token (from `claude setup-token`) in macOS Keychain,
so the nightly launchd job can call Claude with your subscription.

Run `claude setup-token` first, copy the sk-ant-oat01-... token it prints, then run this
and paste it twice when the Keychain asks (input is hidden).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ITEM = ["-s", "family-brief", "-a", "claude-oauth-token"]


def main() -> int:
    print("Paste the token from claude setup-token at each prompt (input is hidden).")
    # A token an earlier version stored trusts only that version's Python. Deleted first, the new
    # item gets this access list, which lets /usr/bin/security read it without a prompt at night.
    subprocess.run(["security", "delete-generic-password", *ITEM], capture_output=True)
    # With nothing after -w, security asks for the token on the terminal itself. Passed as an
    # argument, it would show in `ps` to every account on the Mac.
    stored = subprocess.run(["security", "add-generic-password", "-U", *ITEM,
                             "-T", "/usr/bin/security", "-w"])
    if stored.returncode != 0:
        print("The token wasn't stored.", file=sys.stderr)
        return 1
    found = subprocess.run(["security", "find-generic-password", *ITEM, "-w"],
                           capture_output=True, text=True)
    if found.returncode != 0:
        print("The token was stored but can't be read back: " + found.stderr.strip()[:200], file=sys.stderr)
        return 1
    tok = found.stdout.strip()
    if not tok.startswith("sk-ant-"):
        # Left in place, it would win over an API key and fail every night.
        subprocess.run(["security", "delete-generic-password", *ITEM], capture_output=True)
        print("That doesn't look like a Claude token (it should start with sk-ant-), so it wasn't kept. "
              "Run this again and paste the token claude setup-token printed.", file=sys.stderr)
        return 1
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    proc = subprocess.run(["claude", "-p", "Reply with just the two letters OK", "--output-format", "json",
                           "--no-session-persistence"],
                          capture_output=True, text=True, timeout=120, env=env,
                          stdin=subprocess.DEVNULL)
    try:
        ok = not json.loads(proc.stdout).get("is_error")
    except Exception:
        ok = False
    print("Stored in the Keychain, and the test call worked." if ok
          else "Stored in the Keychain, but the test call failed: " + (proc.stdout or proc.stderr)[:200])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
