"""Manage the kdev block in ~/.ssh/config.

Marker-delimited rewrite rather than parsing Host stanzas: the old
launch_kaggle.py approach of scanning for the next `Host ` line silently ate
the final stanza in the file whenever ours was last.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import cloudflared

BEGIN = "# >>> kdev >>>"
END = "# <<< kdev <<<"
BLOCK_RE = re.compile(rf"\n?{re.escape(BEGIN)}.*?{re.escape(END)}\n?", re.DOTALL)

SSH_CONFIG = Path.home() / ".ssh" / "config"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts.kdev"


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
        f"    UserKnownHostsFile {KNOWN_HOSTS}\n"
        f"    StrictHostKeyChecking accept-new\n"
        f"    ServerAliveInterval 30\n"
        f"    ServerAliveCountMax 10\n"
        f"    RequestTTY yes\n"
        f"{END}\n"
    )


def write(
    alias: str, hostname: str, user: str = "root", cloudflared_path: Path | None = None
) -> Path:
    SSH_CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
    existing = SSH_CONFIG.read_text() if SSH_CONFIG.exists() else ""
    stripped = BLOCK_RE.sub("\n", existing).strip()
    body = (stripped + "\n\n" if stripped else "") + render_block(
        alias, hostname, user, cloudflared_path
    )
    SSH_CONFIG.write_text(body)
    SSH_CONFIG.chmod(0o600)
    # Every session is a brand-new container with a brand-new host key, so a
    # stale entry here is guaranteed, not hypothetical. Scope the reset to our
    # own known_hosts file instead of disabling host checking globally.
    KNOWN_HOSTS.write_text("")
    KNOWN_HOSTS.chmod(0o600)
    return SSH_CONFIG


def has_block() -> bool:
    """Whether ~/.ssh/config currently points the alias at a session."""
    return SSH_CONFIG.exists() and BEGIN in SSH_CONFIG.read_text()


def clear() -> None:
    if SSH_CONFIG.exists():
        SSH_CONFIG.write_text(BLOCK_RE.sub("\n", SSH_CONFIG.read_text()).strip() + "\n")
