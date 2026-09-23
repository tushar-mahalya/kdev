"""Settings and accounts, stored at ~/.config/kdev/config.json (mode 0600).

One entry per Kaggle account. Every account in the group runs the same shared
notebook on its own weekly quota; which one runs a given session is the only
thing that changes when you switch.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .api import Creds
from .errors import KdevError

CONFIG_DIR = Path(os.environ.get("KDEV_CONFIG_DIR", Path.home() / ".config" / "kdev"))
CONFIG_PATH = CONFIG_DIR / "config.json"

#: Bumped when the stored shape changes in a way `load` has to migrate.
SCHEMA = 1


@dataclass
class Profile:
    """One Kaggle account. Its OAuth token lives in creds/<name>.json."""

    username: str
    #: Set by Config so `creds` can find this account's token file.
    name: str = ""

    @property
    def creds(self) -> Creds:
        from . import auth

        token = ""
        if self.name and auth.signed_in(self.name):
            try:
                token = auth.access_token(self.name)
            except auth.AuthError:
                token = ""
        return Creds(self.username, token)


@dataclass
class Config:
    schema: int = SCHEMA
    profiles: dict[str, Profile] = field(default_factory=dict)
    active: str = ""
    #: The one notebook the whole group runs, as "owner/slug". It belongs to the
    #: workspace, not to any account: every member runs it on their own quota.
    notebook: str = ""
    #: Its numeric id, needed for sharing (IAM) calls.
    notebook_id: int = 0
    #: The Kaggle group it is shared with.
    group_slug: str = ""
    #: Public hostname of the Cloudflare named tunnel, e.g. kaggle.example.com.
    tunnel_hostname: str = ""
    #: Connector token, for a tunnel whose ingress lives in the CF dashboard.
    tunnel_token: str = ""
    #: Credentials JSON for a locally-managed tunnel -- what `kdev tunnel
    #: setup` makes, because it needs no dashboard provisioning.
    tunnel_credentials: str = ""
    tunnel_id: str = ""
    #: Tunnel name as it appears in `cloudflared tunnel list`.
    tunnel_name: str = "kdev"
    #: This machine's SSH public key, authorised on every box it starts.
    ssh_public_key: str = ""
    #: Host alias written into ~/.ssh/config.
    ssh_host_alias: str = "kaggle"
    #: The box `kdev up` starts when no flags say otherwise.
    default_gpu: str = ""
    default_hours: float = 0.0
    #: Optional git remote: cloned on boot, pushed every 15 min and on teardown.
    git_remote: str = ""
    #: Private deploy key (contents) for that remote.
    git_deploy_key: str = ""

    def add(self, name: str, prof: Profile) -> Profile:
        prof.name = name
        self.profiles[name] = prof
        self.active = self.active or name
        return prof

    def profile(self, name: str = "") -> Profile:
        name = name or self.active
        if not name:
            raise KdevError("No account yet.", "Sign one in: kdev account add")
        if name not in self.profiles:
            have = ", ".join(self.profiles) or "none"
            raise KdevError(
                f"No account called {name!r} (have: {have}).", "See them with: kdev account"
            )
        prof = self.profiles[name]
        prof.name = name
        return prof


def load() -> Config:
    if not CONFIG_PATH.exists():
        return Config()
    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError) as e:
        raise KdevError(
            f"{CONFIG_PATH} is unreadable ({e}).", "Move it aside and run: kdev setup"
        ) from e
    if not isinstance(raw, dict):
        raise KdevError(f"{CONFIG_PATH} is not a kdev config.", "Move it aside and run: kdev setup")
    # `name` is what Profile.creds uses to find the token file, so it is
    # stamped here: callers that iterate `cfg.profiles` directly would
    # otherwise authenticate as nobody and get a 401.
    raw["profiles"] = {
        k: Profile(
            **{
                **{f: v for f, v in (v or {}).items() if f in Profile.__dataclass_fields__},
                "name": k,
            }
        )
        for k, v in (raw.get("profiles") or {}).items()
    }
    # Unknown keys are dropped rather than rejected, so a config written by an
    # older kdev (API keys, per-account notebooks) still loads.
    cfg = Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})
    cfg.schema = SCHEMA
    if cfg.active and cfg.active not in cfg.profiles:
        cfg.active = next(iter(cfg.profiles), "")
    return cfg


def save(cfg: Config) -> None:
    # A Config with no accounts is almost always half-built (a test, a script),
    # and writing it over a working one would destroy every account, the
    # notebook and the tunnel at once. Refuse unless there is nothing to lose.
    if not cfg.profiles and CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text() or "{}")
        except ValueError:
            existing = {}
        if existing.get("profiles"):
            raise ValueError(
                f"refusing to overwrite {CONFIG_PATH} with a config that has no "
                "accounts; set KDEV_CONFIG_DIR to write somewhere else"
            )
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    for prof in data["profiles"].values():
        prof.pop("name", None)  # derived from the key; storing it invites drift
    # Written through a temp file and renamed: a crash mid-write must never
    # leave a truncated config behind. Created 0600 before any secret lands.
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(CONFIG_PATH)


#: Account names become file names under ~/.config/kdev/creds/, so they cannot
#: be arbitrary text -- an unvalidated prompt once stored a whole Rich panel
#: border as one.
PROFILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def valid_profile_name(name: str) -> str:
    name = (name or "").strip()
    if not PROFILE_NAME_RE.fullmatch(name):
        raise ValueError(
            "Account names are 1-32 letters, digits, dots, dashes or underscores, "
            "starting with a letter or digit."
        )
    return name


def default_ssh_public_key() -> str:
    for name in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"):
        p = Path.home() / ".ssh" / name
        if p.exists():
            return p.read_text().strip()
    return ""
