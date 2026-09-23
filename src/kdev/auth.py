"""Kaggle OAuth, one credential set per profile.

Kaggle deprecated username+key auth for most of its API: the official CLI now
refuses it outright and the endpoints kdev needs (ListKernels, quota, session
status) answer 401. Only a Bearer access token works.

Tokens expire after 12 hours, but the credentials file also carries a
long-lived refresh token, and kagglesdk refreshes transparently. kdev keeps one
credentials file per profile so several teammates' accounts can coexist -- the
official CLI only ever has one, at ~/.kaggle/credentials.json.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as _config

KAGGLE_CREDENTIALS = Path.home() / ".kaggle" / "credentials.json"


class AuthError(RuntimeError):
    pass


def creds_dir() -> Path:
    return _config.CONFIG_DIR / "creds"


def creds_path(profile: str) -> Path:
    return creds_dir() / f"{profile}.json"


def _kaggle_cli() -> list[str]:
    """The official CLI drives the browser flow; we only keep the result."""
    exe = shutil.which("kaggle")
    if exe:
        return [exe]
    return [sys.executable, "-m", "kaggle"]


def login(profile: str) -> str:
    """Run the browser OAuth flow and file the result under this profile.

    Returns the authenticated username. The CLI always writes to the one shared
    path, so the credentials are moved into the profile's own slot immediately
    -- otherwise adding a second account would silently overwrite the first.
    """
    before = KAGGLE_CREDENTIALS.read_text() if KAGGLE_CREDENTIALS.exists() else None
    # The CLI prints a long "you could also use an API token" block before it
    # gets to the browser flow. Capture it; only surface it if login fails, so
    # the user is not asked to choose an auth method kdev has already chosen.
    try:
        proc = subprocess.run(
            [*_kaggle_cli(), "auth", "login", "--force"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        raise AuthError("Timed out waiting for the browser sign-in.") from e
    except OSError as e:
        raise AuthError(f"Could not run the kaggle CLI: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise AuthError(f"Kaggle sign-in did not complete.\n{detail}")
    if not KAGGLE_CREDENTIALS.exists():
        raise AuthError("Login reported success but wrote no credentials.")

    data = json.loads(KAGGLE_CREDENTIALS.read_text())
    if not data.get("refresh_token"):
        raise AuthError("Credentials are missing a refresh token.")

    dest = creds_path(profile)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2))
    dest.chmod(0o600)

    # Put the shared file back so kdev does not disturb the user's own CLI login.
    if before is not None:
        KAGGLE_CREDENTIALS.write_text(before)

    return data.get("username") or ""


def access_token(profile: str) -> str:
    """A valid access token for this profile, refreshing it if it has expired."""
    path = creds_path(profile)
    if not path.exists():
        raise AuthError(f"{profile} is not signed in. Run: kdev account add {profile}")
    try:
        from kagglesdk import KaggleClient
        from kagglesdk.kaggle_creds import KaggleCredentials
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise AuthError(f"kagglesdk is not installed: {e}") from e

    creds = KaggleCredentials.load(KaggleClient(), file_path=str(path))
    if creds is None:
        raise AuthError(f"Could not read credentials for {profile!r}.")
    token = creds.get_access_token()
    if not token:
        raise AuthError(f"Could not refresh {profile}'s sign-in. Run: kdev account add {profile}")
    return token


def signed_in(profile: str) -> bool:
    return creds_path(profile).exists()


def forget(profile: str) -> None:
    creds_path(profile).unlink(missing_ok=True)
