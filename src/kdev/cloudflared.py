"""Locate, or fetch, the local `cloudflared` binary.

It is a Go binary, not a Python package, so it cannot be a project dependency.
There is no official PyPI distribution -- `pycloudflared` is an unofficial
one-person wrapper -- so kdev fetches Cloudflare's own release directly, the
same source Homebrew pulls from.

A system install always wins; the managed copy is a fallback so that
`uv tool install kdev` leaves nothing for the user to go and install by hand.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .errors import KdevError

RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"

#: Kept on `latest` deliberately: bootstrap.py fetches `latest` on the Kaggle
#: side too, and a stale pinned client against a fresh connector is a real
#: failure mode. Override with KDEV_CLOUDFLARED_URL to pin a version.
_ASSETS = {
    ("Darwin", "arm64"): "cloudflared-darwin-arm64.tgz",
    ("Darwin", "x86_64"): "cloudflared-darwin-amd64.tgz",
    ("Linux", "x86_64"): "cloudflared-linux-amd64",
    ("Linux", "aarch64"): "cloudflared-linux-arm64",
    ("Linux", "arm64"): "cloudflared-linux-arm64",
    ("Linux", "armv7l"): "cloudflared-linux-arm",
    ("Windows", "AMD64"): "cloudflared-windows-amd64.exe",
    ("Windows", "x86_64"): "cloudflared-windows-amd64.exe",
}


def bin_dir() -> Path:
    root = os.environ.get("KDEV_DATA_DIR")
    if root:
        return Path(root) / "bin"
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "kdev" / "bin"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "kdev" / "bin"


def managed_path() -> Path:
    name = "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"
    return bin_dir() / name


def asset_name() -> str:
    key = (platform.system(), platform.machine())
    if key not in _ASSETS:
        raise KdevError(
            f"No cloudflared build for {key[0]}/{key[1]}. "
            "Install it yourself and put it on PATH: https://pkg.cloudflare.com"
        )
    return _ASSETS[key]


def find() -> Path | None:
    """System install first, then the copy kdev manages."""
    system = shutil.which("cloudflared")
    if system:
        return Path(system)
    managed = managed_path()
    return managed if managed.exists() and os.access(managed, os.X_OK) else None


def version(path: Path) -> str:
    try:
        out = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def install(progress=None) -> Path:
    """Download the official release for this platform into kdev's data dir."""
    asset = asset_name()
    url = os.environ.get("KDEV_CLOUDFLARED_URL", f"{RELEASE}/{asset}")
    dest = managed_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / asset
        _download(url, tmp_path, progress)
        if asset.endswith(".tgz"):
            # The macOS tarballs hold a single bare `cloudflared` at the root.
            with tarfile.open(tmp_path) as tf:
                member = next(
                    (
                        m
                        for m in tf.getmembers()
                        if m.isfile() and Path(m.name).name.startswith("cloudflared")
                    ),
                    None,
                )
                if member is None:
                    raise KdevError(f"No cloudflared binary inside {asset}")
                # Extract only the one member we want, and never let the archive
                # choose where it lands.
                handle = tf.extractfile(member)
                if handle is None:
                    raise KdevError(f"Could not read cloudflared out of {asset}")
                extracted = Path(tmp) / "cloudflared"
                with handle, extracted.open("wb") as out:
                    shutil.copyfileobj(handle, out)
            shutil.move(str(extracted), dest)
        else:
            shutil.move(str(tmp_path), dest)

    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # A binary that downloads but will not run is worse than no binary: it fails
    # later, inside ssh's ProxyCommand, where the error is unreadable.
    if version(dest) == "unknown":
        dest.unlink(missing_ok=True)
        raise KdevError(f"Downloaded cloudflared from {url} but it would not execute.")
    return dest


def _download(url: str, dest: Path, progress=None) -> str:
    sha = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as f:
            while chunk := r.read(262144):
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return sha.hexdigest()


def ensure(auto: bool = True, progress=None) -> Path:
    """Return a usable cloudflared, downloading it if allowed."""
    found = find()
    if found:
        return found
    if not auto:
        raise KdevError(
            "cloudflared not found. Run `kdev doctor --install` or "
            "install it yourself: brew install cloudflared"
        )
    return install(progress)


def proxy_command(path: Path | None = None) -> str:
    """ssh runs ProxyCommand through /bin/sh, so the path must survive a shell.

    A bare `cloudflared` would only resolve for a system install; kdev's managed
    copy lives outside PATH, so the absolute path is written instead -- quoted,
    because home directories and app-support paths do contain spaces.
    """
    # Never downloads: rendering an ssh config must not reach the network.
    # A bare name is the honest fallback -- if no cloudflared is resolvable,
    # ssh will say so, which beats stalling on a 19 MB fetch nobody asked for.
    path = path or find()
    if path is None:
        return "cloudflared access ssh --hostname %h"
    p = str(path)
    if any(c in p for c in " \t\"'\\$`"):
        p = (
            '"'
            + p.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
            + '"'
        )
    return f"{p} access ssh --hostname %h"
