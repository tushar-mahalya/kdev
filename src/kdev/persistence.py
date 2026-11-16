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
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import api
from . import config as _config
from .errors import KdevError

REMOTE = "/kaggle/working"

#: Kept out of the local backup. `.kdev` is the live session's own state:
#: pushing an old copy back up would tell the box it is restored when it is not.
#: A leading / anchors a name to the top, where the box writes it; a project's
#: own web/static/custom.css is still backed up.
EXCLUDES = [
    "/cloudflared.log",
    "/.kdev-session",
    "/.kdev-stop",
    "/.kdev",
    "/__notebook__.ipynb",
    "/__output__.json",
    "/custom.css",
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
FETCHED = ".kdev/fetched"

#: How far back to look for a version to build on. A handful of cut-short
#: sessions in a row is already unusual; ten means something else is wrong.
WALK_BACK = 10
# Concurrent submissions that never reached an elected startup do not count
# against the saved-workspace history window.
MAX_UNCONFIRMED = 100
#: Layers stacked when the newest version was never fully restored. Each one
#: is a complete listing, so this bounds the plan's size.
MAX_LAYERS = 4


def version_files(creds: api.Creds, notebook: str, label: str) -> dict[str, str] | None:
    """name -> signed URL for one saved version, or None if there is none."""
    try:
        files = api.session_output(creds, notebook, label)
    except api.KaggleError as e:
        if e.status == 404:
            return None
        raise  # An unreadable history is not an empty workspace.
    return {f["name"]: f["url"] for f in files}


def version_meta(creds: api.Creds, notebook: str, label: str) -> dict[str, str] | None:
    """Like `version_files`, but only `.kdev/` is guaranteed complete.

    Enough to read a version's saved state and tell whether it holds files,
    for a fraction of the calls: a 2,400-file workspace lists in five pages
    of about 2s each, and its `.kdev/` names all sit on the first.
    """
    try:
        files = api.session_output(creds, notebook, label, prefix=".kdev/")
    except api.KaggleError as e:
        if e.status == 404:
            return None
        raise
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
    label, files = newest_saved(creds, notebook, latest)
    if not label:
        return []
    state = _state(files)
    if state and (state.get("restored") is False or state.get("missing")):
        base = [str(x) for x in state.get("layers") or []]
        return [*base, label][-MAX_LAYERS:]
    return [label]


def newest_saved(creds: api.Creds, notebook: str, latest: int) -> tuple[str, dict[str, str]]:
    """The newest version, from `latest` back, that holds any files.

    A running session's own version has none until it ends, and neither has a
    Quick Save of the source: neither is where your files are.
    """
    number, remaining, unconfirmed = latest, WALK_BACK, 0
    while number > 0 and remaining > 0:
        files = version_meta(creds, notebook, f"v{number}")
        if files:
            state = _state(files)
            if STATE in files and state is None:
                raise KdevError(
                    f"Could not read the saved state of v{number}.",
                    "Retry before starting a box; its restore history could not be verified.",
                )
            if state and state.get("startup_confirmed") is False:
                unconfirmed += 1
                if unconfirmed > MAX_UNCONFIRMED:
                    raise KdevError("Too many unconfirmed starts to safely find your saved files.")
            else:
                return f"v{number}", files
        else:
            remaining -= 1
        number -= 1
    return "", {}


_ENDED = frozenset({"COMPLETE", "ERROR", "CANCEL_ACKNOWLEDGED"})
_cache_lock = threading.Lock()


def _history_cache_path() -> Path:
    return _config.CONFIG_DIR / "cache" / "history.json"


def _history_cache() -> dict:
    try:
        data = json.loads(_history_cache_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_history_cache(cache: dict) -> None:
    path = _history_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache, sort_keys=True))
        tmp.replace(path)
    except OSError:
        pass  # only a cache


def file_count(creds: api.Creds, notebook: str, label: str) -> int:
    """How many of your files a saved version holds -- kdev's own `.kdev/`
    bookkeeping excluded, the same count `kdev history` shows. An ended
    version's count is remembered, so this is usually free."""
    hit = _history_cache().get(f"{notebook}@{label}")
    if isinstance(hit, dict) and hit.get("status") in _ENDED and isinstance(hit.get("files"), int):
        return hit["files"]
    files = version_files(creds, notebook, label) or {}
    return sum(not name.startswith(".kdev/") for name in files)


def session_history(creds: api.Creds, notebook: str, latest: int, limit: int = 10) -> list[dict]:
    """Recent saved sessions, newest first.

    Empty/source-only versions are skipped, matching the restore walk. State
    metadata comes from the version itself and run status is requested for the
    same version label.
    """

    cache = _history_cache()

    def read(label: str) -> dict | None:
        key = f"{notebook}@{label}"
        hit = cache.get(key)
        if isinstance(hit, dict) and hit.get("status") in _ENDED:
            return dict(hit)
        # Status before the listing: a version still saving when listed may
        # end a moment later, and its partial file count must not be cached
        # as final. Only a version that had ended before it was listed is.
        try:
            status = api.session_status(creds, notebook, label)
        except api.KaggleError:
            status = {"status": "UNKNOWN"}
        files = version_files(creds, notebook, label)
        if not files:
            return None
        state = _state(files) or {}
        row = {
            "version": label,
            "run_by": state.get("run_by") or None,
            "started": state.get("started") or None,
            "ends": state.get("ends") or None,
            "status": status.get("status") or "UNKNOWN",
            "files": sum(not name.startswith(".kdev/") for name in files),
            "restored": state.get("restored") if "restored" in state else None,
            "restore_finished": state.get("finished") or None,
        }
        if row["status"] in _ENDED:
            # A version that has ended never changes again: remember it.
            with _cache_lock:
                cache[key] = row
        return row

    # Empty versions do not count towards `limit`, so the window must be
    # wider than it -- but bounded, since each version costs a full listing.
    window = max(WALK_BACK, limit * 2)
    labels = [f"v{n}" for n in range(latest, max(0, latest - window), -1)]
    rows: list[dict] = []
    # Read a batch at once, newest first in the result: each version is
    # several seconds of paging, and none depends on another. Later batches
    # are only read while `limit` is not yet met.
    batch = max(WALK_BACK, limit)
    # Four at a time: more finishes no sooner (each version is paged in
    # order) and trips Kaggle's rate limit.
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(labels)))) as pool:
        for start in range(0, len(labels), batch):
            for row in pool.map(read, labels[start : start + batch]):
                if row and len(rows) < limit:
                    rows.append(row)
            if len(rows) >= limit:
                break
    _save_history_cache(cache)
    return rows


def _fetched(files: dict[str, str]) -> set[str]:
    """The names a version's restore delivered (its .kdev/fetched)."""
    url = files.get(FETCHED)
    if not url:
        return set()
    try:
        r = httpx.get(url, follow_redirects=True, timeout=60)
        lines = r.text.splitlines() if r.status_code == 200 else []
        return {json.loads(line) for line in lines if line.strip()}
    except (httpx.HTTPError, ValueError):
        return set()


def _still_needed(top: dict[str, str]):
    """Which files of the layers under `top` it still needs, or None for all.

    A layer is stacked under the newest only because the newest never got all
    of it. Taking the rest too would bring back whatever was deleted since.
    """
    state = _state(top) or {}
    if state.get("restored") and state.get("missing"):
        return set(state["missing"]).__contains__
    if state.get("restored") is False:
        got = _fetched(top)
        if got:
            return lambda name: name not in got
    return None


def build_plan(creds: api.Creds, notebook: str, labels: list[str]) -> dict:
    """Fresh signed URLs for each layer, resolved just before they are used."""
    # Listed together: each is a few seconds of paging, and none depends on another.
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(labels)))) as pool:
        listed = list(
            zip(
                labels, pool.map(lambda lb: version_files(creds, notebook, lb), labels), strict=True
            )
        )
    listed = [(label, files) for label, files in listed if files]
    needed = _still_needed(listed[-1][1]) if len(listed) > 1 else None
    layers = []
    for i, (label, files) in enumerate(listed):
        if needed and i < len(listed) - 1:
            # .kdev/ carries the metadata and keys every layer contributes.
            files = {n: u for n, u in files.items() if n.startswith(".kdev/") or needed(n)}
        layers.append({"label": label, "files": files})
    return {"layers": layers}


def _ssh(
    alias: str, command: str, stdin: str | None = None, timeout: float = 120
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", alias, command],
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


def confirm_start(alias: str, sid: str, timeout: int = 60) -> bool:
    """Confirm startup only on the session selected from version-specific logs."""
    if not sid.isdigit() or int(sid) <= 0:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = _ssh(
                alias,
                f"/root/.kdev/run --confirm-start {sid}",
                timeout=min(10, max(0.1, deadline - time.monotonic())),
            )
            if r.returncode == 0:
                return True
            # Boxes started by an older kdev have no confirmation command/flag.
            if r.returncode == 2:
                r = _ssh(
                    alias,
                    f"cat {REMOTE}/{STATE}",
                    timeout=min(10, max(0.1, deadline - time.monotonic())),
                )
                state = json.loads(r.stdout) if r.returncode == 0 else {}
                if not isinstance(state, dict):
                    return False
                return state.get("session") == sid and "startup_confirmed" not in state
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    return False


def restore_on_box(
    alias: str, plan: dict, progress=None, *, session_id: str = ""
) -> tuple[bool, dict]:
    """Start the restore on the box, then follow it until it finishes.

    The box runs it detached, so if this process dies -- laptop asleep, wifi
    gone -- the restore carries on, and `kdev restore` picks the progress up
    again from any machine.
    """
    from . import bootstrap

    if not wait_reachable(alias):
        return False, {"error": "ssh never became reachable"}
    payload = json.dumps(
        {"script": bootstrap.BOX_SCRIPT.read_text(), "plan": plan, "session": session_id}
    )
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
                "-T",
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


#: The longest one sync may take. A stalled tunnel otherwise hangs forever.
RSYNC_TIMEOUT = 1800


def _rsync(src: str, dst: str, *extra: str, excludes: bool = True) -> subprocess.CompletedProcess:
    cmd = ["rsync", "-az", "--partial", *extra, "-e", "ssh -o BatchMode=yes -o ConnectTimeout=20"]
    if excludes:
        for pattern in EXCLUDES:
            cmd += ["--exclude", pattern]
    cmd += [src, dst]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT)
    except subprocess.TimeoutExpired:
        # Reported like any failed sync: push and pull keep their (ok, err)
        # answer, and --partial lets the next attempt pick up where this left.
        return subprocess.CompletedProcess(
            cmd, 124, "", f"rsync timed out after {RSYNC_TIMEOUT // 60} min"
        )
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", f"could not run rsync: {e}")


def wait_reachable(alias: str, timeout: int = 180) -> bool:
    """Block until ssh can actually open a channel.

    The tunnel announces itself the moment cloudflared connects, but sshd on
    the far side may not be accepting yet -- pushing straight away failed
    silently and left the box empty.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # ConnectTimeout covers the TCP connect only; a ProxyCommand that
            # connects and then stalls would hang here without a cap.
            r = subprocess.run(
                ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, "true"],
                capture_output=True,
                timeout=30,
            )
            if r.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
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
        # Pushing a backup fills in; it never replaces what this session wrote.
        r = _rsync(f"{local}/", f"{alias}:{REMOTE}/", "--ignore-existing")
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or "").strip()[-200:]
        time.sleep(3 * (attempt + 1))
    return False, err


def pull(alias: str) -> tuple[bool, str]:
    """Bring the session's working directory down into the mirror."""
    local = mirror_dir()
    local.mkdir(parents=True, exist_ok=True)
    # An exact copy of the box: without --delete, files removed on the box --
    # or left from another notebook -- linger here and get pushed back up.
    r = _rsync(f"{alias}:{REMOTE}/", f"{local}/", "--delete")
    return r.returncode == 0, (r.stderr or "").strip()[-200:]


def contents() -> list[str]:
    local = mirror_dir()
    if not local.is_dir():
        return []
    return sorted(str(p.relative_to(local)) for p in local.rglob("*") if p.is_file())
