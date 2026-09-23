"""`kdev setup`: get this machine ready, doing only what is missing.

Safe to re-run at any point: every step checks first and leaves a working
piece alone. On a second machine joining an existing workspace, the tunnel
comes from the notebook itself, so Cloudflare setup is skipped entirely.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import auth, cloudflared, config, tunnel, ui
from . import notebook as nb
from .errors import KdevError


def setup_named_tunnel(cfg: config.Config, hostname: str = "", force: bool = False) -> bool:
    """Provision the tunnel with the cloudflared CLI. Returns True on success.

    Locally-managed, so the ingress rule ships with the Kaggle container and
    nothing needs provisioning in the Zero Trust dashboard.
    """
    if not force and not hostname:
        with ui.spinner("checking your existing tunnel…"):
            already = tunnel.is_configured(cfg)
        if already:
            ui.ok(f"{cfg.tunnel_hostname} is already set up")
            ui.hint("Nothing to change. Use --force to rebuild it.")
            return True

    if not tunnel.logged_in():
        ui.hint("A browser will open so you can authorise your Cloudflare account.")
        if not ui.confirm("Continue?", default=True):
            return False
        try:
            tunnel.login()
        except tunnel.TunnelError as e:
            ui.err(str(e))
            return False

    with ui.spinner("reading your Cloudflare zone…"):
        zone = tunnel.zone_name()

    if hostname:
        # --hostname overrides the derived name, for a zone layout that needs it.
        cfg.tunnel_hostname = tunnel.validate_hostname(hostname, zone)
        cfg.tunnel_name = cfg.tunnel_name or cfg.tunnel_hostname.split(".")[0]
    elif zone:
        ui.ok(f"zone  {zone}")
        # Look for a tunnel that already has a matching DNS record before
        # asking for a name -- otherwise re-running setup invents a second one.
        with ui.spinner("checking for an existing tunnel…"):
            found = tunnel.existing_for_zone(zone)
        if found and not force:
            pick = found[0] if len(found) == 1 else None
            if pick is None:
                choice = ui.choose(
                    "Reuse an existing tunnel?",
                    [(f["hostname"], f["hostname"]) for f in found] + [("", "Create a new one")],
                )
                pick = next((f for f in found if f["hostname"] == choice), None)
            elif not ui.confirm(f"Reuse {pick['hostname']}?", default=True):
                pick = None
            if pick:
                cfg.tunnel_name, cfg.tunnel_id = pick["name"], pick["id"]
                cfg.tunnel_hostname = pick["hostname"]
                try:
                    cfg.tunnel_credentials = tunnel.credentials(pick["name"])
                except tunnel.TunnelError as e:
                    ui.err(str(e))
                    return False
                cfg.tunnel_token = ""
                ui.ok(f"{cfg.tunnel_hostname}  (reused existing tunnel and DNS record)")
                return True
        while True:
            try:
                cfg.tunnel_name = tunnel.validate_label(
                    ui.ask(
                        f"Subdomain for the box (becomes <name>.{zone})",
                        default=cfg.tunnel_name or "kdev",
                    )
                )
                break
            except tunnel.TunnelError as e:
                ui.err(str(e))
        cfg.tunnel_hostname = f"{cfg.tunnel_name}.{zone}"
    else:
        ui.warn("Could not read your zone name.")
        while True:
            try:
                cfg.tunnel_hostname = tunnel.validate_hostname(
                    ui.ask("Hostname you will ssh to", default=cfg.tunnel_hostname)
                )
                break
            except tunnel.TunnelError as e:
                ui.err(str(e))
                if not ui.confirm("Try again?", default=True):
                    return False
        cfg.tunnel_name = cfg.tunnel_name or cfg.tunnel_hostname.split(".")[0]

    def on_conflict(record: dict) -> bool:
        ui.warn(f"{cfg.tunnel_hostname} already points at {record.get('content')}")
        return ui.confirm("Repoint it at this tunnel?", default=False)

    try:
        with ui.spinner("setting up tunnel…"):
            cfg.tunnel_id, cfg.tunnel_credentials, dns = tunnel.provision(
                cfg.tunnel_name, cfg.tunnel_hostname, on_conflict
            )
    except tunnel.TunnelError as e:
        ui.err(str(e))
        return False

    cfg.tunnel_token = ""
    explain = {
        "reused": "DNS record already correct, left alone",
        "created": "DNS record created",
        "repointed": "DNS record repointed at this tunnel",
    }
    ui.ok(f"{cfg.tunnel_hostname}  ({explain.get(dns, dns)})")
    return True


def choose_tunnel(cfg: config.Config) -> None:
    """Named or quick tunnel.

    They ride the same connector and edge, so throughput and latency are the
    same. The difference is a drop: a named tunnel comes back on the same
    hostname, a quick one on a new random hostname, stranding a session that
    is still using quota until `kdev up` finds it again.
    """
    mode = ui.choose(
        "How should the box be reachable?",
        [
            ("named", "Your Cloudflare domain   same hostname every time  (recommended)"),
            ("quick", "Free quick tunnel        new hostname every session"),
        ],
    )
    if mode == "named" and setup_named_tunnel(cfg):
        return
    if mode == "named":
        ui.warn("Using a quick tunnel for now; `kdev tunnel setup` retries the named one.")
    cfg.tunnel_token = cfg.tunnel_credentials = cfg.tunnel_id = ""
    cfg.tunnel_hostname = ""


def ensure_ssh_key(cfg: config.Config) -> None:
    if not cfg.ssh_public_key:
        cfg.ssh_public_key = config.default_ssh_public_key()
    if cfg.ssh_public_key:
        comment = cfg.ssh_public_key.split()[-1] if " " in cfg.ssh_public_key else "found"
        ui.ok(f"ssh key      {comment}")
        return
    ui.warn("No SSH key in ~/.ssh")
    if not ui.confirm("Generate one now?", default=True):
        raise KdevError(
            "kdev needs an SSH key to let you into the box.", "Make one with: ssh-keygen -t ed25519"
        )
    path = Path.home() / ".ssh" / "id_ed25519"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-f", str(path), "-N", ""], check=True)
    cfg.ssh_public_key = path.with_suffix(".pub").read_text().strip()
    ui.ok("ssh key      generated ~/.ssh/id_ed25519")


def ensure_cloudflared() -> None:
    found = cloudflared.find()
    if found:
        ui.ok(f"cloudflared  {cloudflared.version(found)}")
        return
    ui.warn("cloudflared is missing; ssh uses it to reach the box")
    if not ui.confirm(
        f"Download Cloudflare's official build ({cloudflared.asset_name()})?", default=True
    ):
        raise KdevError("kdev needs cloudflared.", "Install it: brew install cloudflared")
    path = ui.download("cloudflared", cloudflared.install)
    ui.ok(f"cloudflared  {cloudflared.version(path)}")


def sign_in(cfg: config.Config, name: str = "") -> str:
    """Sign a Kaggle account in through the browser. Returns its account name."""
    while not name:
        try:
            name = config.valid_profile_name(ui.ask("Name for this account", default="me"))
        except ValueError as e:
            ui.err(str(e))
    name = config.valid_profile_name(name)
    ui.hint("Your browser will open to sign in to Kaggle.")
    try:
        username = auth.login(name)
    except auth.AuthError as e:
        raise KdevError(str(e), f"Try again with: kdev account add {name}") from e
    if not username:
        raise KdevError(
            "Kaggle did not say which account signed in.",
            f"Try again with: kdev account add {name}",
        )
    clash = next((n for n, p in cfg.profiles.items() if p.username == username and n != name), "")
    if clash:
        auth.forget(name)
        raise KdevError(
            f"{username} is already signed in here as {clash!r}.",
            f"Use it with: kdev account use {clash}",
        )
    cfg.add(name, config.Profile(username=username))
    return name


def run() -> config.Config:
    ui.header("setup")
    cfg = config.load()

    ensure_ssh_key(cfg)
    ensure_cloudflared()

    if cfg.profiles:
        ui.ok(f"accounts     {', '.join(cfg.profiles)}")
    else:
        ui.blank()
        name = sign_in(cfg)
        ui.ok(f"account      {name} ({cfg.profiles[name].username})")
    config.save(cfg)

    # Workspace before tunnel: a second machine joining an existing workspace
    # takes the tunnel the notebook already runs, and skips Cloudflare setup.
    if cfg.notebook:
        ui.ok(f"workspace    {cfg.notebook}")
    else:
        ui.blank()
        prof = cfg.profile()
        if not nb.join(cfg, prof.creds, prof.username):
            raise KdevError("No workspace chosen.", "Pick one later with: kdev workspace join")
    taken = nb.adopt_config(cfg, cfg.profile().creds)
    config.save(cfg)

    if cfg.tunnel_credentials or cfg.tunnel_token:
        source = "from the notebook" if any(k.startswith("tunnel_") for k in taken) else "set up"
        ui.ok(f"tunnel       {cfg.tunnel_hostname or 'token'} ({source})")
    else:
        ui.blank()
        choose_tunnel(cfg)
    config.save(cfg)

    ui.blank()
    ui.ok("ready", f"settings saved to {config.CONFIG_PATH}")
    ui.blank()
    ui.next_steps(
        [("kdev up", "start the box"), ("kdev account add", "add another account to share quota")]
    )
    return cfg
