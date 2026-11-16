"""`kdev doctor`: check every piece kdev depends on, and fix what it can."""

from __future__ import annotations

import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import quote

import typer
from rich.text import Text

from .. import api, auth, cloudflared, config, sshcfg, ui
from .. import tunnel as cftunnel


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    warn_only: bool = False
    group: str = "This machine"


def _account_check(name: str, prof: config.Profile) -> Check:
    if not auth.signed_in(name):
        return Check(name, False, "not signed in", f"kdev account add {name}", group="Accounts")
    try:
        api.quota(prof.creds)
        return Check(name, True, prof.username, group="Accounts")
    except (api.KaggleError, auth.AuthError) as e:
        if _offline(e):
            return Check(
                name, False, "could not reach Kaggle", "check your connection", group="Accounts"
            )
        return Check(name, False, _short(e), f"kdev account add {name}", group="Accounts")


def _offline(e: Exception) -> bool:
    return isinstance(e, auth.AuthUnavailable) or (isinstance(e, api.KaggleError) and not e.status)


def _short(e: Exception, width: int = 60) -> str:
    """An error's first clause, cut at a word rather than mid-word."""
    text = str(e).split(" (")[0]
    return text if len(text) <= width else text[:width].rsplit(" ", 1)[0] + "…"


def _checks(cfg: config.Config) -> list[Check]:
    out: list[Check] = []
    py = sys.version_info
    out.append(
        Check(
            "python",
            py >= (3, 11),
            f"{py.major}.{py.minor}.{py.micro}",
            "kdev needs Python 3.11 or newer",
        )
    )
    for tool, why in (("ssh", "to reach the box"), ("rsync", "for kdev backup")):
        path = shutil.which(tool)
        out.append(
            Check(tool, bool(path), path or f"not found; needed {why}", warn_only=tool == "rsync")
        )
    cf = cloudflared.find()
    if cf:
        found = re.search(r"\d{4}\.\d+\.\d+", cloudflared.version(cf))
        detail = found.group(0) if found else cloudflared.version(cf)
    else:
        detail = "missing"
    out.append(Check("cloudflared", bool(cf), detail, "kdev doctor --fix"))
    out.append(
        Check(
            "ssh key",
            bool(cfg.ssh_public_key),
            (cfg.ssh_public_key.split()[-1] if " " in cfg.ssh_public_key else "set")
            if cfg.ssh_public_key
            else "none",
            "kdev setup",
        )
    )
    if not cfg.profiles:
        out.append(Check("accounts", False, "none", "kdev account add", group="Accounts"))
    # One round trip per account, all at once: a slow account must not make
    # the whole check wait N times over.
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(cfg.profiles)))) as pool:
        out += list(pool.map(lambda item: _account_check(*item), cfg.profiles.items()))
    if cfg.notebook and cfg.profiles:
        try:
            meta = api.get_kernel(cfg.profile().creds, cfg.notebook)
            out.append(Check("workspace", bool(meta), cfg.notebook, group="Workspace"))
        except (api.KaggleError, auth.AuthError) as e:
            offline = _offline(e)
            out.append(
                Check(
                    "workspace",
                    False,
                    f"{cfg.notebook}: " + ("could not reach Kaggle" if offline else _short(e, 50)),
                    "check your connection" if offline else "kdev workspace join",
                    group="Workspace",
                )
            )
    else:
        out.append(Check("workspace", False, "none", "kdev workspace join", group="Workspace"))
    if cfg.tunnel_credentials or cfg.tunnel_token:
        detail = cfg.tunnel_hostname or "token"
        good = True
        unread = False
        if cfg.tunnel_credentials and cftunnel.logged_in():
            body = cftunnel._api(f"/dns_records?name={quote(cfg.tunnel_hostname)}")
            if not body.get("success"):
                # Unreachable is not "wrong": don't send anyone to rebuild a tunnel.
                unread, good = True, False
                detail += " (DNS not checked: could not reach Cloudflare)"
            else:
                rec = next(iter(body.get("result") or []), None)
                good = bool(rec and rec.get("content") == cftunnel.tunnel_target(cfg.tunnel_id))
                detail += "" if good else " (DNS does not point at it)"
        out.append(
            Check(
                "tunnel",
                good,
                detail,
                "" if unread else "kdev tunnel setup --force",
                warn_only=unread,
                group="Workspace",
            )
        )
    else:
        out.append(
            Check(
                "tunnel",
                True,
                "quick (new hostname each session)",
                "kdev tunnel setup",
                warn_only=True,
                group="Workspace",
            )
        )
    out.append(
        Check(
            "ssh config",
            True,
            f"{cfg.ssh_host_alias} "
            + ("present" if sshcfg.has_block() else "written on the next kdev up"),
            group="Workspace",
        )
    )
    return out


def doctor(
    fix: bool = typer.Option(False, "--fix", help="Install what is missing where kdev can."),
    upgrade: bool = typer.Option(
        False, "--upgrade-cloudflared", help="Re-download kdev's cloudflared."
    ),
) -> None:
    """Check everything kdev depends on. Exits 1 if something is broken."""
    cfg = config.load()
    ui.header("doctor", terminal_only=True)
    if upgrade or (fix and not cloudflared.find()):
        path = ui.download("cloudflared", cloudflared.install)
        ui.ok(f"installed cloudflared {cloudflared.version(path)}")
    with ui.spinner("checking…"):
        checks = _checks(cfg)
    width = max(len(c.name) for c in checks)
    groups = list(dict.fromkeys(c.group for c in checks))
    for n, group in enumerate(groups):
        if n:
            ui.blank()
        ui.section(group)
        for c in (c for c in checks if c.group == group):
            label = Text.assemble((f"{c.name:<{width}}  ", "bold"), (c.detail, ""))
            if c.ok and not (c.warn_only and "quick" in c.detail):
                ui.ok(label)
            elif c.warn_only:
                ui.warn(label, f"{' ' * (width + 4)}{c.fix}" if c.fix else "")
            else:
                ui.err(label, f"{' ' * (width + 4)}fix: {c.fix}" if c.fix else "")
    broken = [c for c in checks if not c.ok and not c.warn_only]
    ui.blank()
    if broken:
        ui.err(f"{len(broken)} problem(s) to fix")
        raise typer.Exit(1)
    ui.ok(Text("everything kdev needs is in place", style="kdev.ok"))
