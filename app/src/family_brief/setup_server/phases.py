"""The setup page's phases, in setup's order. The server answers each one's calls and puts
together `api/state` from each one's part, so adding a phase here is all it takes."""
from __future__ import annotations

from .ai import AISignIn
from .chat import Chat
from .check import Check
from .connect import Connect
from .first_brief import FirstBrief
from .finish import Finish
from .gmail import Gmail
from .health import Health
from .myclub import MyClub
from .welcome import Welcome
from .whatsapp import WhatsApp
from .wilma import Wilma

PHASES = (Welcome, Connect, Wilma, Gmail, AISignIn, WhatsApp, MyClub, Check, Health, FirstBrief,
          Finish, Chat)
