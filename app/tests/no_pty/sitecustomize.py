"""Where no pseudo-terminal can be opened (a sandbox, for one), test_setup_wilma.py puts this
folder on the Wilma window's PYTHONPATH, so `pty.fork` connects the fake wilma CLI through a
socket pair instead. The window's code reads and writes it the same way."""
import os
import pty
import socket


def fork() -> tuple[int, int]:
    ours, theirs = socket.socketpair()
    pid = os.fork()
    if pid == 0:
        os.setsid()
        ours.close()
        for n in (0, 1, 2):
            os.dup2(theirs.fileno(), n)
        theirs.close()
        return 0, -1
    theirs.close()
    return pid, ours.detach()


pty.fork = fork
