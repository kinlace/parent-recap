"""setup page — serve the setup page on this Mac and open it in the browser (ADR 0006).

The page is plain HTML, CSS and JavaScript in `page/`, shipped with the program. It talks to this
server through a small JSON API under the same address, one call per action, and the server saves
the answers through `setup save`, so the page and the chat setup write the same config.

`server` is the HTTP shell: the one-time code, the safeguards and idle shutdown. Each phase of
setup is a module of its own, answering its calls, and `phases` lists them; what they share is the
`Context` each is given.
"""
from .context import Action, Context, Phase, Timeouts
from .phases import PHASES
from .server import SetupServer, closed_line, register, serve

__all__ = ["PHASES", "Action", "Context", "Phase", "SetupServer", "Timeouts", "closed_line",
           "register", "serve"]
