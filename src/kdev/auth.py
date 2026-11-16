"""Kaggle OAuth, one credential set per profile.

Kaggle deprecated username+key auth for most of its API: the official CLI now
refuses it outright and the endpoints kdev needs (ListKernels, quota, session
status) answer 401. Only a Bearer access token works.

Tokens expire after 12 hours, but the credentials file also carries a
long-lived refresh token, which kdev trades for a new access token when the
stored one is within an hour of expiring. kdev keeps one
credentials file per profile so several teammates' accounts can coexist -- the
official CLI only ever has one, at ~/.kaggle/credentials.json.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import api
from . import config as _config

KAGGLE_CREDENTIALS = Path.home() / ".kaggle" / "credentials.json"
#: The RPC service that turns a refresh token into an access token.
ACCOUNTS = "users.AccountService"


class AuthError(RuntimeError):
    """This account cannot authenticate: not signed in, or its sign-in is
    no longer accepted. Signing in again fixes it."""


class AuthUnavailable(AuthError):
    """The token could not be refreshed right now -- offline, a proxy, Kaggle
    down. Signing in again would not help, so callers must not treat this as
    "signed out"."""


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

    _write_private(creds_path(profile), data)
    invalidate(profile)

    # Put the shared file back so kdev does not disturb the user's own CLI login.
    if before is not None:
        KAGGLE_CREDENTIALS.write_text(before)

    return data.get("username") or ""


#: Refresh a token this long before it expires. Longer than any one wait
#: that holds a single `Creds` (a box boot or a save: up to 30 min), so a
#: token never lapses halfway through and fails as "signed out".
REFRESH_MARGIN = timedelta(hours=1)
#: What kagglesdk asks for; Kaggle's answer says what it actually granted.
TOKEN_LIFETIME = timedelta(hours=12)

#: profile creds path -> (file identity, token, expiry). One process reads an
#: account's token dozens of times; minting one costs a round trip each.
_cache: dict[str, tuple[tuple[int, int, int], str, datetime]] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    # One lock per account, so several accounts still mint in parallel.
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _identity(path: Path) -> tuple[int, int, int]:
    """Changes whenever the file is replaced -- a re-login, another kdev."""
    st = path.stat()
    return st.st_ino, st.st_mtime_ns, st.st_size


def _expiry(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _fresh(expires: datetime) -> bool:
    return expires - datetime.now(UTC) > REFRESH_MARGIN


def invalidate(profile: str) -> None:
    """Forget the cached token, after the account's file was replaced."""
    _cache.pop(str(creds_path(profile)), None)


def _write_private(path: Path, data: dict) -> None:
    """Write JSON through a temp file and a rename, 0600 from the start.

    Several threads, or kdev processes, can refresh one account at once; a
    half-written file would sign the account out.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _mint(profile: str, refresh_token: str) -> tuple[str, datetime]:
    """A new access token from the refresh token. Nothing is written here.

    The same request kagglesdk's `KaggleCredentials.generate_access_token`
    builds (users.AccountService/GenerateAccessToken), sent directly: the SDK's
    refresh path saves the result to ~/.kaggle/credentials.json -- the user's
    own CLI login -- sends with no timeout, and attaches whatever other
    credentials it finds in the environment.
    """
    payload = {
        "refreshToken": refresh_token,
        "apiVersion": "API_VERSION_V1",
        "expirationDuration": f"{int(TOKEN_LIFETIME.total_seconds())}s",
    }
    try:
        resp = api.call(api.Creds(profile), "GenerateAccessToken", payload, service=ACCOUNTS)
    except api.KaggleError as e:
        if e.status in (400, 401, 403, 404):
            raise AuthError(
                f"Kaggle no longer accepts {profile}'s sign-in. Run: kdev account add {profile}"
            ) from e
        raise AuthUnavailable(
            f"Could not reach Kaggle to refresh {profile}'s sign-in ({e})."
        ) from e
    token = str(resp.get("token") or "")
    if not token:
        raise AuthUnavailable(f"Kaggle sent no access token for {profile}.")
    try:
        seconds = int(resp.get("expiresIn") or 0)
    except (TypeError, ValueError):
        seconds = 0
    lifetime = timedelta(seconds=seconds) if seconds > 0 else TOKEN_LIFETIME
    return token, datetime.now(UTC) + lifetime


def access_token(profile: str) -> str:
    """A valid access token for this profile, refreshing it when near expiry.

    Read once per process and kept, so the dozens of calls one command makes
    share a token. A refresh lands in the profile's own file, never in the
    official CLI's.
    """
    path = creds_path(profile)
    key = str(path)
    with _lock_for(key):
        try:
            ident = _identity(path)
        except FileNotFoundError:
            _cache.pop(key, None)
            raise AuthError(
                f"{profile} is not signed in. Run: kdev account add {profile}"
            ) from None
        except OSError as e:
            raise AuthError(f"Could not read credentials for {profile!r}: {e}") from e
        hit = _cache.get(key)
        if hit and hit[0] == ident and _fresh(hit[2]):
            return hit[1]

        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise AuthError(f"Could not read credentials for {profile!r}: {e}") from e
        if not isinstance(data, dict) or not data.get("refresh_token"):
            raise AuthError(
                f"{profile}'s credentials have no refresh token. Run: kdev account add {profile}"
            )
        token = str(data.get("access_token") or "")
        expires = _expiry(data.get("access_token_expiration"))
        if not token or expires is None or not _fresh(expires):
            token, expires = _mint(profile, str(data["refresh_token"]))
            data["access_token"] = token
            data["access_token_expiration"] = expires.isoformat()
            try:
                _write_private(path, data)
                ident = _identity(path)
            except OSError:
                # The token is good either way; the next process mints again.
                pass
        _cache[key] = (ident, token, expires)
        return token


def signed_in(profile: str) -> bool:
    return creds_path(profile).exists()


def forget(profile: str) -> None:
    creds_path(profile).unlink(missing_ok=True)
    invalidate(profile)
