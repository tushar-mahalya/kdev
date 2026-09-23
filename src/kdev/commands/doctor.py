"""`kdev doctor`: check every piece kdev depends on, and fix what it can."""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass

import typer

from .. import api, auth, cloudflared, config, sshcfg, ui
from .. import tunnel as cftunnel


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    warn_only: bool = False


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
        out.append(Check("accounts", False, "none", "kdev account add"))
    for name, prof in cfg.profiles.items():
        if not auth.signed_in(name):
            out.append(Check(f"account {name}", False, "not signed in", f"kdev account add {name}"))
            continue
        try:
            api.quota(prof.creds)
            out.append(Check(f"account {name}", True, prof.username))
        except api.KaggleError as e:
            out.append(Check(f"account {name}", False, str(e)[:60], f"kdev account add {name}"))
    if cfg.notebook and cfg.profiles:
        try:
            meta = api.get_kernel(cfg.profile().creds, cfg.notebook)
            out.append(Check("workspace", bool(meta), cfg.notebook))
        except api.KaggleError as e:
            out.append(
                Check("workspace", False, f"{cfg.notebook}: {str(e)[:50]}", "kdev workspace join")
            )
    else:
        out.append(Check("workspace", False, "none", "kdev workspace join"))
    if cfg.tunnel_credentials or cfg.tunnel_token:
        detail = cfg.tunnel_hostname or "token"
        good = True
        if cfg.tunnel_credentials and cftunnel.logged_in():
            rec = cftunnel.dns_record(cfg.tunnel_hostname)
            good = bool(rec and rec.get("content") == cftunnel.tunnel_target(cfg.tunnel_id))
            detail += "" if good else " (DNS does not point at it)"
        out.append(Check("tunnel", good, detail, "kdev tunnel setup --force"))
    else:
        out.append(Check("tunnel", True, "quick (new hostname each session)", warn_only=True))
    out.append(
        Check(
            "ssh config",
            True,
            f"{cfg.ssh_host_alias} "
            + ("present" if sshcfg.has_block() else "written on the next kdev up"),
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
    if upgrade or (fix and not cloudflared.find()):
        path = ui.download("cloudflared", cloudflared.install)
        ui.ok(f"installed cloudflared {cloudflared.version(path)}")
    with ui.spinner("checking…"):
        checks = _checks(cfg)
    width = max(len(c.name) for c in checks)
    for c in checks:
        label = f"{c.name:<{width}}  {c.detail}"
        if c.ok and not (c.warn_only and "quick" in c.detail):
            ui.ok(label)
        elif c.warn_only:
            ui.warn(label)
        else:
            ui.err(label, c.fix)
    broken = [c for c in checks if not c.ok and not c.warn_only]
    ui.blank()
    if broken:
        ui.err(f"{len(broken)} problem(s)")
        raise typer.Exit(1)
    ui.ok("everything kdev needs is in place")
