"""Manage the kdev block in ~/.ssh/config.

Marker-delimited rewrite rather than parsing Host stanzas: the old
launch_kaggle.py approach of scanning for the next `Host ` line silently ate
the final stanza in the file whenever ours was last.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import cloudflared

BEGIN = "# >>> kdev >>>"
END = "# <<< kdev <<<"
BLOCK_RE = re.compile(rf"\n?{re.escape(BEGIN)}.*?{re.escape(END)}\n?", re.DOTALL)

SSH_CONFIG = Path.home() / ".ssh" / "config"


def render_block(
    alias: str, hostname: str, user: str = "root", cloudflared_path: Path | None = None
) -> str:
    proxy = cloudflared.proxy_command(cloudflared_path)
    return (
        f"{BEGIN}\n"
        f"Host {alias}\n"
        f"    HostName {hostname}\n"
        f"    User {user}\n"
        f"    ProxyCommand {proxy}\n"
        # Every session is a new container with a new host key, so a key
        # remembered by one machine is wrong on the next box another machine
        # starts: VS Code and `ssh kaggle` there fail until someone resets it.
        # Only a box holding the workspace's tunnel credentials can answer on
        # this hostname, and whoever holds those can already edit the
        # notebook that builds the box, so a pinned key protects nothing.
        f"    UserKnownHostsFile {os.devnull}\n"
        f"    StrictHostKeyChecking no\n"
        f"    LogLevel ERROR\n"
        f"    ServerAliveInterval 30\n"
        f"    ServerAliveCountMax 10\n"
        # For `ssh kaggle htop`. kdev's own background ssh calls pass -T: a tty
        # there puts this terminal in raw mode and smears the live board.
        f"    RequestTTY yes\n"
        f"{END}\n"
    )


#: A line that opens a stanza. ssh takes the first value it finds for each
#: option, so a block below a `Host *` loses every option that one sets.
_STANZA_RE = re.compile(r"^\s*(host|match)(\s|=)", re.IGNORECASE)


def _without_block(text: str) -> str:
    """The file with kdev's block cut out, and the gap it leaves closed."""
    while m := BLOCK_RE.search(text):
        before, after = text[: m.start()].rstrip("\n"), text[m.end() :].lstrip("\n")
        text = before + ("\n\n" if before and after else "\n" if before else "") + after
    return text


def _insert_at(lines: list[str]) -> int:
    """Where the block goes: above the first stanza, and above the comments
    directly on top of it, which belong to that stanza. Global options and
    `Include`s before any stanza stay first; they apply to every host."""
    for i, line in enumerate(lines):
        if _STANZA_RE.match(line):
            while i > 0 and lines[i - 1].lstrip().startswith("#"):
                i -= 1
            return i
    return len(lines)


def write(
    alias: str, hostname: str, user: str = "root", cloudflared_path: Path | None = None
) -> Path:
    """Put the block in ~/.ssh/config ahead of every other stanza.

    Written in place, not via a rename, so a symlinked config (dotfiles)
    stays a symlink. An earlier block -- even one below a `Host *` -- is
    moved, not duplicated; nothing else in the file changes.
    """
    SSH_CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
    existing = SSH_CONFIG.read_text() if SSH_CONFIG.exists() else ""
    lines = _without_block(existing).strip("\n").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    at = _insert_at(lines)
    head, tail = "".join(lines[:at]).rstrip("\n"), "".join(lines[at:]).lstrip("\n")
    block = render_block(alias, hostname, user, cloudflared_path)
    body = (head + "\n\n" if head else "") + block + ("\n" + tail if tail else "")
    SSH_CONFIG.write_text(body)
    SSH_CONFIG.chmod(0o600)
    return SSH_CONFIG


def current() -> tuple[str, str] | None:
    """(alias, hostname) of the block in ~/.ssh/config, or None if none."""
    if not SSH_CONFIG.exists():
        return None
    m = BLOCK_RE.search(SSH_CONFIG.read_text())
    if not m:
        return None
    alias = re.search(r"^\s*Host\s+(\S+)", m.group(0), re.MULTILINE)
    host = re.search(r"^\s*HostName\s+(\S+)", m.group(0), re.MULTILINE)
    return (alias.group(1) if alias else "", host.group(1) if host else "")


def has_block() -> bool:
    """Whether ~/.ssh/config currently points the alias at a session."""
    return SSH_CONFIG.exists() and BEGIN in SSH_CONFIG.read_text()


def clear() -> None:
    if SSH_CONFIG.exists():
        SSH_CONFIG.write_text(_without_block(SSH_CONFIG.read_text()).strip("\n") + "\n")
