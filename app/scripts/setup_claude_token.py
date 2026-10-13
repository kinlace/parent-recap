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

sys.path.insert(0, "src")
from family_brief.system import secrets  # noqa

ACCOUNT = "claude-oauth-token"


def main() -> int:
    print("Paste the token from claude setup-token at each prompt (input is hidden).")
    # Typed at the Keychain's own prompt, the token is on no command line, where `ps` would show
    # it to every account on the Mac. A token an earlier version stored is replaced, so the
    # evening job reads the new one without a prompt.
    try:
        secrets.put_typed(ACCOUNT)
    except secrets.SecretsError:
        print("The token wasn't stored.", file=sys.stderr)
        return 1
    try:
        tok = (secrets.get(ACCOUNT) or "").strip()
    except secrets.SecretsError as e:
        print("The token was stored but can't be read back: " + str(e)[:200], file=sys.stderr)
        return 1
    if not tok.startswith("sk-ant-"):
        # Left in place, it would win over an API key and fail every night.
        try:
            secrets.delete(ACCOUNT)
        except secrets.SecretsError:
            pass
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
    print(f"Stored in the {secrets.STORE}, and the test call worked." if ok
          else f"Stored in the {secrets.STORE}, but the test call failed: " + (proc.stdout or proc.stderr)[:200])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
