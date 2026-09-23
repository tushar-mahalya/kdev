"""One error type for everything a user can act on.

Every failure kdev can explain carries a message (what went wrong, one line)
and optionally a hint (what to run about it). `cli.main` renders all of them
the same way and exits 1 -- so no command prints a raw traceback, a bare
`SystemExit` string, or a JSON blob from an API.
"""

from __future__ import annotations


class KdevError(Exception):
    """A failure the user can do something about."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class Cancelled(KdevError):
    """The user backed out of a prompt. Not an error; exits quietly."""

    def __init__(self) -> None:
        super().__init__("cancelled")
