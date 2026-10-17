"""The window `claude setup-token` runs in: `python -m family_brief.claude_window PROGRAM
PASTE_INTO`, given the claude program and where the family pastes the token. Once they press
Enter it clears the screen and the scrollback, so the token isn't left behind in the window.

A module rather than `python -c` with a script, since Windows PowerShell drops the script's
double quotes on the way to Python (#376)."""
from __future__ import annotations

import subprocess
import sys


def main(argv: list[str]) -> None:
    program, paste_into = argv
    print("Parent Recap: sign in to Claude for the nightly Brief.")
    print("Click Authorize on the page that opens in your browser. If no page opens, open the")
    print("link shown below.")
    print(f"Copy the token this window then shows and paste it in {paste_into}.")
    print(flush=True)  # ahead of claude's own output, also where this output isn't a console
    try:
        subprocess.run([program, "setup-token"])
        print()
        print(f"When the token is in {paste_into}, press Enter here to clear it from")
        print("this window.")
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    print("\033[H\033[2J\033[3J", end="")
    print("You can close this window.")


if __name__ == "__main__":
    main(sys.argv[1:])
