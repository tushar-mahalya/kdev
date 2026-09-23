"""Create and route a Cloudflare named tunnel entirely from the CLI.

Cloudflare has two tunnel modes and they are not interchangeable:

  * remotely-managed (`run --token`): ingress rules live in the Zero Trust
    dashboard, and a local config file is ignored.
  * locally-managed (`run --credentials-file` + `--config`): ingress comes from
    a config file next to the connector.

kdev uses the locally-managed mode. The remote side is a Kaggle container we
generate from scratch every session, so shipping it a six-line config is easier
than making the user provision ingress by hand -- and it means the whole setup
is scriptable, with no Zero Trust onboarding at all.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import cloudflared

CERT = Path.home() / ".cloudflared" / "cert.pem"


class TunnelError(RuntimeError):
    pass


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    cf = cloudflared.ensure(auto=False)
    return subprocess.run([str(cf), *args], text=True, capture_output=True, **kw)


def logged_in() -> bool:
    return CERT.exists()


def login() -> None:
    """Opens a browser so the user can pick the zone. Writes ~/.cloudflared/cert.pem."""
    cf = cloudflared.ensure(auto=False)
    # Inherit stdio: this prints a URL and waits for the browser round-trip.
    proc = subprocess.run([str(cf), "tunnel", "login"])
    if proc.returncode != 0 or not CERT.exists():
        raise TunnelError("cloudflared login did not complete.")


def list_tunnels() -> list[dict]:
    out = _run(["tunnel", "list", "--output", "json"])
    if out.returncode != 0:
        raise TunnelError(out.stderr.strip() or "could not list tunnels")
    try:
        return json.loads(out.stdout or "[]") or []
    except json.JSONDecodeError:
        return []


def find_tunnel(name: str) -> dict | None:
    return next((t for t in list_tunnels() if t.get("name") == name), None)


def create(name: str) -> str:
    """Create the tunnel, or reuse it if the name is already taken. Returns its UUID."""
    existing = find_tunnel(name)
    if existing:
        return existing["id"]
    out = _run(["tunnel", "create", name])
    if out.returncode != 0:
        raise TunnelError(out.stderr.strip() or f"could not create tunnel {name!r}")
    found = find_tunnel(name)
    if not found:
        raise TunnelError(f"created {name!r} but it does not appear in the tunnel list")
    return found["id"]


def _api(path: str) -> dict:
    """Read the Cloudflare API with the token cert.pem already carries."""
    tok = _cert_token()
    if not tok.get("apiToken") or not tok.get("zoneID"):
        return {}
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/zones/{tok['zoneID']}{path}",
        headers={"Authorization": f"Bearer {tok['apiToken']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except (urllib.error.URLError, ValueError, TimeoutError):
        return {}


def dns_record(hostname: str) -> dict | None:
    """The existing DNS record for a hostname, if there is one."""
    body = _api(f"/dns_records?name={urllib.parse.quote(hostname)}")
    if not body.get("success"):
        return None
    return next(iter(body.get("result") or []), None)


def tunnel_target(tunnel_id: str) -> str:
    return f"{tunnel_id}.cfargotunnel.com"


def route_dns(name: str, hostname: str, tunnel_id: str = "", on_conflict=None) -> str:
    """Point hostname at the tunnel, but only if it is not already pointed there.

    Returns "reused", "created" or "repointed". The previous version passed
    --overwrite-dns unconditionally, which rewrote a correct record on every
    run and would have silently clobbered an unrelated one.
    """
    target = tunnel_target(tunnel_id) if tunnel_id else ""
    existing = dns_record(hostname) if target else None

    if existing and existing.get("content") == target:
        return "reused"

    if existing and on_conflict and not on_conflict(existing):
        raise TunnelError(f"{hostname} already points at {existing.get('content')}")

    args = ["tunnel", "route", "dns"]
    if existing:
        args.append("--overwrite-dns")
    out = _run([*args, name, hostname])
    if out.returncode != 0:
        msg = (out.stderr or out.stdout).strip()
        if "already exists" not in msg.lower():
            raise TunnelError(msg or f"could not route {hostname}")
    return "repointed" if existing else "created"


def credentials(name: str) -> str:
    """Fetch the tunnel's credentials JSON, which the Kaggle side runs with."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cred.json"
        out = _run(["tunnel", "token", "--cred-file", str(path), name])
        if out.returncode != 0:
            raise TunnelError(out.stderr.strip() or "could not fetch tunnel credentials")
        if not path.exists():
            raise TunnelError("cloudflared reported success but wrote no credentials file")
        blob = path.read_text().strip()
    parsed = json.loads(blob)
    for key in ("AccountTag", "TunnelSecret", "TunnelID"):
        if key not in parsed:
            raise TunnelError(f"credentials JSON is missing {key}")
    return blob


def _cert_token() -> dict:
    """cert.pem is a PEM-wrapped base64 JSON blob: {accountID, apiToken, zoneID}."""
    if not CERT.exists():
        return {}
    b64 = "".join(ln for ln in CERT.read_text().splitlines() if not ln.startswith("-----"))
    try:
        return json.loads(base64.b64decode(b64))
    except (ValueError, json.JSONDecodeError):
        return {}


def zone_name() -> str:
    """Resolve the authorised zone's name.

    cert.pem carries a zone *ID*, not a name, so the name has to be looked up.
    The api token in the same file is scoped for exactly this.
    """
    tok = _cert_token()
    if not tok.get("zoneID") or not tok.get("apiToken"):
        return ""
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/zones/{tok['zoneID']}",
        headers={"Authorization": f"Bearer {tok['apiToken']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.load(r)
        return body["result"]["name"] if body.get("success") else ""
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return ""


def validate_label(label: str) -> str:
    """A single DNS label: what the box is called, and its subdomain."""
    label = label.strip().lower()
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", label or ""):
        raise TunnelError(f"{label!r} is not a valid name. Use letters, digits and hyphens.")
    return label


def validate_hostname(hostname: str, zone: str = "") -> str:
    """Reject anything that is not a real subdomain of the authorised zone.

    A bare label like `kdev` looks plausible next to the tunnel-name prompt but
    is not routable: `tunnel route dns` needs an FQDN inside a zone you own.
    """
    hostname = hostname.strip().lower().rstrip(".")
    if not hostname:
        raise TunnelError("Hostname cannot be empty.")
    if "." not in hostname:
        raise TunnelError(
            f"{hostname!r} is a bare label, not a hostname. "
            f"Use a subdomain such as kaggle.{zone or 'yourdomain.com'}"
        )
    if not re.fullmatch(
        r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", hostname
    ):
        raise TunnelError(f"{hostname!r} is not a valid DNS name.")
    if zone and not (hostname == zone or hostname.endswith("." + zone)):
        raise TunnelError(
            f"{hostname!r} is not inside your Cloudflare zone {zone!r}. Try kaggle.{zone}"
        )
    return hostname


def provision(name: str, hostname: str, on_conflict=None) -> tuple[str, str, str]:
    """Create, route and fetch credentials. Returns (id, creds_json, dns_status)."""
    tunnel_id = create(name)
    status = route_dns(name, hostname, tunnel_id, on_conflict)
    return tunnel_id, credentials(name), status


def existing_for_zone(zone: str) -> list[dict]:
    """Tunnels on this account that already have a DNS record in this zone.

    Used to offer a reuse instead of asking for a name and creating a duplicate.
    """
    if not zone:
        return []
    try:
        candidates = list_tunnels()
    except TunnelError:
        return []
    out = []
    for t in candidates:
        host = f"{t.get('name')}.{zone}"
        rec = dns_record(host)
        if rec and rec.get("content") == tunnel_target(t.get("id", "")):
            out.append({"name": t.get("name"), "id": t.get("id"), "hostname": host})
    return out


def is_configured(cfg) -> bool:
    """True when the saved tunnel still exists on the account and DNS matches."""
    if not (cfg.tunnel_credentials and cfg.tunnel_id and cfg.tunnel_hostname):
        return False
    if not logged_in():
        return False
    try:
        if not any(t.get("id") == cfg.tunnel_id for t in list_tunnels()):
            return False
    except TunnelError:
        return False
    rec = dns_record(cfg.tunnel_hostname)
    return bool(rec and rec.get("content") == tunnel_target(cfg.tunnel_id))
