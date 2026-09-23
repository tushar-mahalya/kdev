"""Where your workspace lives between sessions, and how it gets back.

The notebook's saved versions are the source of truth. Kaggle commits
/kaggle/working as a version whenever a batch run ends -- a clean exit, a crash
and a cancel were all measured to commit, up to the moment the container
stopped -- and every version stays readable afterwards. That store belongs to
the *notebook*, not to an account or a machine, so any account in the group,
on any laptop, sees the same thing.

kdev's job is to put it back. It finds the right saved version here, where the
Kaggle login lives, and hands the box a plan; the box downloads straight from
Kaggle (see kdev_box.py). Nothing routes through your laptop, and nothing on
your laptop can override it.

The local mirror is a backup and nothing more: `kdev backup` copies a live box
down, `kdev restore --from-backup` pushes it back up on request.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx

from . import api
from . import config as _config

REMOTE = "/kaggle/working"

#: Kept out of the local backup. `.kdev` is the live session's own state:
#: pushing an old copy back up would tell the box it is restored when it is not.
EXCLUDES = [
    "cloudflared.log",
    ".kdev-session",
    ".kdev-stop",
    ".kdev",
    "__notebook__.ipynb",
    "__output__.json",
    "custom.css",
    ".virtual_documents",
    ".ipynb_checkpoints",
    "__pycache__",
]


def mirror_dir() -> Path:
    """Where the workspace lives on this machine.

    One directory for everything, deliberately NOT keyed to the notebook:
    notebooks get recreated and switched constantly, and keying the mirror to
    them meant every new notebook handed you an empty workspace -- which looked
    exactly like the sync being broken.
    """
    return _config.CONFIG_DIR / "workspace"


STATE = ".kdev/state.json"

#: How far back to look for a version to build on. A handful of cut-short
#: sessions in a row is already unusual; ten means something else is wrong.
WALK_BACK = 10
#: Layers stacked when the newest version was never fully restored. Each one
#: is a complete listing, so this bounds the plan's size.
MAX_LAYERS = 4


def version_files(creds: api.Creds, notebook: str, label: str) -> dict[str, str] | None:
    """name -> signed URL for one saved version, or None if there is none."""
    try:
        files = api.session_output(creds, notebook, label)
    except api.KaggleError:
        return None
    return {f["name"]: f["url"] for f in files}


def _state(files: dict[str, str]) -> dict | None:
    """The .kdev/state.json a version saved, fetched through its own URL."""
    url = files.get(STATE)
    if not url:
        return None
    try:
        r = httpx.get(url, follow_redirects=True, timeout=30)
        data = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def plan_layers(creds: api.Creds, notebook: str, latest: int) -> list[str]:
    """Which saved versions, oldest first, rebuild the workspace as you left it.

    Walks back from `latest`:
      * a version with no files was not a working session (a Quick Save of the
        source, the placeholder `workspace create` made) -- skip it;
      * a version whose state says it was fully restored, or that kdev never
        touched, is complete on its own -- use just that;
      * a version that was never fully restored (killed mid-restore, or nobody
        ran the restore before working in it) still holds whatever was done in
        it -- stack it on the layers it was meant to be built on.
    """
    for number in range(latest, max(0, latest - WALK_BACK), -1):
        label = f"v{number}"
        files = version_files(creds, notebook, label)
        if not files:
            continue
        state = _state(files)
        if state and (state.get("restored") is False or state.get("missing")):
            base = [str(x) for x in state.get("layers") or []]
            return [*base, label][-MAX_LAYERS:]
        return [label]
    return []


def build_plan(creds: api.Creds, notebook: str, labels: list[str]) -> dict:
    """Fresh signed URLs for each layer, resolved just before they are used."""
    layers = []
    for label in labels:
        files = version_files(creds, notebook, label)
        if files:
            layers.append({"label": label, "files": files})
    return {"layers": layers}


def _ssh(
    alias: str, command: str, stdin: str | None = None, timeout: int = 120
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", alias, command],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def box_state(alias: str) -> dict:
    """The live session's .kdev/state.json, read over ssh."""
    try:
        r = _ssh(alias, f"cat {REMOTE}/{STATE}", timeout=40)
    except (subprocess.SubprocessError, OSError):
        return {}
    try:
        data = json.loads(r.stdout) if r.returncode == 0 else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def restore_on_box(alias: str, plan: dict, progress=None) -> tuple[bool, dict]:
    """Start the restore on the box, then follow it until it finishes.

    The box runs it detached, so if this process dies -- laptop asleep, wifi
    gone -- the restore carries on, and `kdev restore` picks the progress up
    again from any machine.
    """
    from . import bootstrap

    if not wait_reachable(alias):
        return False, {"error": "ssh never became reachable"}
    payload = json.dumps({"script": bootstrap.BOX_SCRIPT.read_text(), "plan": plan})
    try:
        r = _ssh(alias, "/root/.kdev/run --receive", stdin=payload, timeout=300)
    except (subprocess.SubprocessError, OSError) as e:
        return False, {"error": str(e)}
    if r.returncode != 0:
        return False, {"error": (r.stderr or r.stdout).strip()[-300:]}
    return follow(alias, progress)


def follow(alias: str, progress=None) -> tuple[bool, dict]:
    """Stream a running restore's progress. Returns (restored, last state)."""
    last: dict = {}
    try:
        proc = subprocess.Popen(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=20",
                "-o",
                "ServerAliveInterval=15",
                alias,
                "/root/.kdev/run --watch",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as e:
        return False, {"error": str(e)}
    for line in proc.stdout or []:
        try:
            last = json.loads(line)
        except ValueError:
            continue
        if progress:
            progress(last)
    proc.wait()
    return bool(last.get("restored")), last


def _rsync(src: str, dst: str, excludes: bool = True) -> subprocess.CompletedProcess:
    cmd = ["rsync", "-az", "--partial", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=20"]
    if excludes:
        for pattern in EXCLUDES:
            cmd += ["--exclude", pattern]
    cmd += [src, dst]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1800)


def wait_reachable(alias: str, timeout: int = 180) -> bool:
    """Block until ssh can actually open a channel.

    The tunnel announces itself the moment cloudflared connects, but sshd on
    the far side may not be accepting yet -- pushing straight away failed
    silently and left the box empty.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, "true"],
            capture_output=True,
        )
        if r.returncode == 0:
            return True
        time.sleep(5)
    return False


def push(alias: str, attempts: int = 3) -> tuple[bool, str]:
    """Send the local mirror up to the session."""
    local = mirror_dir()
    local.mkdir(parents=True, exist_ok=True)
    if not any(local.iterdir()):
        return True, "nothing saved yet"
    if not wait_reachable(alias):
        return False, "ssh never became reachable"
    err = ""
    for attempt in range(attempts):
        r = _rsync(f"{local}/", f"{alias}:{REMOTE}/")
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or "").strip()[-200:]
        time.sleep(3 * (attempt + 1))
    return False, err


def pull(alias: str) -> tuple[bool, str]:
    """Bring the session's working directory down into the mirror."""
    local = mirror_dir()
    local.mkdir(parents=True, exist_ok=True)
    r = _rsync(f"{alias}:{REMOTE}/", f"{local}/")
    return r.returncode == 0, (r.stderr or "").strip()[-200:]


def contents() -> list[str]:
    local = mirror_dir()
    if not local.is_dir():
        return []
    return sorted(str(p.relative_to(local)) for p in local.rglob("*") if p.is_file())
