"""The half of kdev that runs on the Kaggle box. Standard library only.

Shipped into the container by the bootstrap cell (and refreshed by `kdev`
over ssh), so everything that has to happen *on* the box lives here, in one
module that the test suite can run locally against a temp directory.

What it is for: making `/kaggle/working` behave like a disk that is always
there. Kaggle commits it as the notebook's version output whenever a batch run
ends -- a clean exit, a crash and a cancel were all measured to commit, right
up to the moment the container stopped. What Kaggle does *not* keep is
symlinks, empty directories and executable bits, and what it cannot do is put
the files back. This module does both:

  * `record_meta()` notes symlinks, empty directories and executable files in
    `.kdev/meta.json`, on a timer, so a kill loses at most one interval of
    metadata and none of the files;
  * `restore()` rebuilds the workspace from a plan of saved versions that kdev
    resolves on your machine, where the Kaggle login lives.

Restore is safe to run at any point in a session, for two reasons:

  * a file that already existed when the restore started is never touched --
    it was written in *this* session, so it is newer than anything saved;
  * `.kdev/state.json` says `restored: false` until every layer is in place.
    A session that dies half-way, or that nobody ever restored, leaves that
    behind, and the next restore stacks this session's own saved files on top
    of the layers it was meant to have. Nothing is skipped, so nothing done in
    the meantime is lost.

Usage on the box:
    kdev_box.py PLAN.json   restore from a plan
    kdev_box.py --receive   read {"script", "plan"} on stdin, start a detached restore
    kdev_box.py --watch     stream restore progress until it finishes
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

STATE = ".kdev/state.json"
META = ".kdev/meta.json"
KEYS = ".kdev/authorized_keys"

#: The box's own runtime files. Restoring them would overwrite the live
#: session's copies with a dead session's.
RUNTIME = {
    "cloudflared.log",
    ".kdev-stop",
    ".kdev-session",
    "__notebook__.ipynb",
    "__output__.json",
    "custom.css",
}

WORKERS = 16
ATTEMPTS = 3


def work_dir() -> Path:
    return Path(os.environ.get("KDEV_WORK", "/kaggle/working"))


def run_dir() -> Path:
    """Scratch space outside /kaggle/working, so none of it is ever committed."""
    return Path(os.environ.get("KDEV_RUN", "/root/.kdev"))


def ssh_dir() -> Path:
    return Path(os.environ.get("KDEV_SSH_DIR", "/root/.ssh"))


def _write_json(path: Path, data: dict) -> None:
    """Atomic, so a kill mid-write never leaves half a JSON file committed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def safe_rel(name: str) -> Path | None:
    """A saved name, or None if it would land outside the workspace."""
    rel = Path(name)
    if not name or rel.is_absolute() or ".." in rel.parts:
        return None
    return rel


def _is_kdev_path(rel: Path) -> bool:
    return bool(rel.parts) and rel.parts[0] == ".kdev"


# --- boot ---------------------------------------------------------------------


def init_state(layers: list[str], session: str = "", run_by: str = "", notebook: str = "") -> None:
    """First thing a session does: say whether it still needs a restore.

    Written before anything slow, so even a session killed during apt-get
    records which saved versions it was supposed to be built on.
    """
    _write_json(
        work_dir() / STATE,
        {
            "restored": not layers,
            "layers": list(layers),
            "session": session,
            "run_by": run_by,
            # Which notebook these version labels belong to. A machine pointed at
            # a different notebook must not resolve "v8" against its own.
            "notebook": notebook,
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def update_state(**fields) -> None:
    """Add facts to the state file as the session learns them (e.g. when it ends)."""
    path = work_dir() / STATE
    state = _read_json(path)
    state.update(fields)
    _write_json(path, state)


def _key_lines(text: str) -> list[str]:
    return [
        ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]


def install_keys(*extra: str) -> list[str]:
    """Authorise every machine that has ever started this workspace.

    The list lives in the workspace itself, so it is saved and restored with
    everything else: a laptop that started a session last week can still reach
    one a teammate's account started today, without anyone copying keys.
    """
    persisted = work_dir() / KEYS
    keys = _key_lines(persisted.read_text()) if persisted.exists() else []
    for block in extra:
        for key in _key_lines(block):
            if key not in keys:
                keys.append(key)
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_text("".join(k + "\n" for k in keys))

    auth = ssh_dir() / "authorized_keys"
    auth.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    auth.write_text("".join(k + "\n" for k in keys))
    auth.chmod(0o600)
    return keys


# --- metadata Kaggle drops ----------------------------------------------------


def _restore_running() -> bool:
    """Whether a restore is running now.

    A pid file alone is not proof: a restore killed hard leaves it behind, and
    over a long session another process can inherit that pid -- which would
    silently stop metadata being recorded for the rest of the session. On the
    box, /proc says what the process actually is.
    """
    try:
        pid = int((run_dir() / "restore.pid").read_text())
    except (OSError, ValueError):
        return False
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.exists():
        try:
            return b"kdev_box.py" in cmdline.read_bytes()
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def scan_meta(work: Path | None = None) -> dict:
    """Symlinks, empty directories and executable files under the workspace."""
    work = work or work_dir()
    symlinks: dict[str, str] = {}
    dirs: list[str] = []
    executable: list[str] = []
    for root, dirnames, filenames in os.walk(work):
        base = Path(root)
        rel_root = base.relative_to(work)
        if rel_root.parts[:1] == (".kdev",):
            dirnames[:] = []
            continue
        for d in list(dirnames):
            p = base / d
            if p.is_symlink():
                # os.walk lists a symlinked directory as a directory; record it
                # as the link it is, and do not descend into its target.
                symlinks[str(p.relative_to(work))] = os.readlink(p)
                dirnames.remove(d)
        if not dirnames and not filenames and rel_root.parts:
            dirs.append(str(rel_root))
        for f in filenames:
            p = base / f
            rel = str(p.relative_to(work))
            try:
                if p.is_symlink():
                    symlinks[rel] = os.readlink(p)
                elif os.stat(p).st_mode & 0o111:
                    executable.append(rel)
            except OSError:
                continue  # removed while we looked; the next pass will be right
    return {"symlinks": symlinks, "dirs": sorted(dirs), "exec": sorted(executable)}


def record_meta() -> bool:
    """Save the metadata Kaggle's snapshot drops. Called on a timer and at exit.

    Skipped while a restore is running: a half-restored tree would record a
    half-set of links, and the restore writes the merged set itself when it
    finishes.
    """
    if _restore_running():
        return False
    _write_json(work_dir() / META, scan_meta())
    return True


# --- restore ------------------------------------------------------------------


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".kdev-part")
    try:
        with urllib.request.urlopen(url, timeout=300) as r, tmp.open("wb") as out:
            shutil.copyfileobj(r, out, 1 << 20)
        # Rename last: a half-written file must never look like a whole one.
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def _fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def _existing(work: Path) -> set[str]:
    """Everything this session already wrote, which a restore must not touch."""
    out = set()
    for p in work.rglob("*"):
        rel = p.relative_to(work)
        if _is_kdev_path(rel):
            continue
        if p.is_symlink() or p.is_file():
            out.add(str(rel))
    return out


def _merge_meta(metas: list[dict], owner: dict[str, int]) -> dict:
    """One metadata set from every layer, each path decided by the layer that
    supplied it: a later regular file beats an earlier symlink, and an exec bit
    only applies to the copy it was recorded on."""
    symlinks: dict[str, tuple[int, str]] = {}
    dirs: set[str] = set()
    executable: set[str] = set()
    for i, meta in enumerate(metas):
        for path, target in (meta.get("symlinks") or {}).items():
            symlinks[path] = (i, target)
        dirs.update(meta.get("dirs") or [])
        executable.update(p for p in meta.get("exec") or [] if owner.get(p) == i)
    return {
        "symlinks": {p: t for p, (i, t) in symlinks.items() if owner.get(p, -1) <= i},
        "dirs": sorted(dirs),
        "exec": sorted(executable),
    }


def apply_meta(meta: dict, preexisting: set[str], work: Path | None = None) -> list[str]:
    """Put back what Kaggle's snapshot dropped. Returns the paths it could not.

    Each path stands alone: a name that now clashes with something else is
    reported and skipped, never allowed to abort the rest.
    """
    work = work or work_dir()
    failed: list[str] = []
    for d in meta.get("dirs") or []:
        rel = safe_rel(d)
        if rel is None:
            continue
        try:
            (work / rel).mkdir(parents=True, exist_ok=True)
        except OSError:
            failed.append(d)
    for name in meta.get("exec") or []:
        rel = safe_rel(name)
        if rel is None or name in preexisting:
            continue
        p = work / rel
        try:
            if p.is_file() and not p.is_symlink():
                p.chmod(p.stat().st_mode | 0o111)
        except OSError:
            failed.append(name)
    for name, target in (meta.get("symlinks") or {}).items():
        rel = safe_rel(name)
        if rel is None or name in preexisting:
            continue
        p = work / rel
        try:
            if p.is_symlink() or p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.symlink_to(target)
        except OSError:
            failed.append(name)
    return failed


def restore(plan: dict, fetch=_fetch, fetch_bytes=_fetch_bytes) -> dict:
    """Rebuild the workspace from `plan["layers"]`, oldest first.

    Each layer is {"label": "v7", "files": {name: signed_url}} exactly as
    Kaggle's own listing returned it. Returns the final state.
    """
    work = work_dir()
    layers = plan.get("layers") or []
    labels = [layer.get("label", "?") for layer in layers]
    preexisting = _existing(work)

    state = _read_json(work / STATE)
    state.update({"restored": False, "layers": labels, "done": 0, "missing": []})

    # Later layers win, so each name is fetched once, from its newest copy.
    wanted: dict[str, tuple[int, str]] = {}
    specials: dict[str, list[str]] = {META: [], KEYS: []}
    for i, layer in enumerate(layers):
        for name, url in (layer.get("files") or {}).items():
            rel = safe_rel(name)
            if rel is None:
                continue
            if name in specials:
                specials[name].append(url)
                continue
            if _is_kdev_path(rel) or rel.name in RUNTIME or name in preexisting:
                continue
            wanted[name] = (i, url)
    state["total"] = len(wanted)
    _write_json(work / STATE, state)

    lock = threading.Lock()
    last_write = [0.0]

    def one(item: tuple[str, tuple[int, str]]) -> None:
        name, (_i, url) = item
        for attempt in range(ATTEMPTS):
            try:
                fetch(url, work / name)
                break
            except Exception:  # per-file boundary: any failure means "missing", recorded below
                if attempt == ATTEMPTS - 1:
                    with lock:
                        state["missing"].append(name)
                    return
                time.sleep(2**attempt)
        with lock:
            state["done"] += 1
            if time.time() - last_write[0] > 0.5:
                last_write[0] = time.time()
                _write_json(work / STATE, state)

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(one, wanted.items()))

    owner = {name: i for name, (i, _url) in wanted.items()}
    metas = []
    for url in specials[META]:
        try:
            metas.append(json.loads(fetch_bytes(url)))
        except (OSError, urllib.error.URLError, ValueError):
            metas.append({})
    merged = _merge_meta(metas, owner)
    unapplied = apply_meta(merged, preexisting, work)
    # Written by the restore rather than left to the timer, so a kill in the
    # next few seconds still commits the full set -- and scanned from disk, not
    # copied from the layers, so links and exec bits this session already made
    # before the restore ran are recorded too.
    _write_json(work / META, scan_meta(work))

    for url in specials[KEYS]:
        with contextlib.suppress(OSError, urllib.error.URLError, ValueError):
            install_keys(fetch_bytes(url).decode())

    if unapplied:
        state["unapplied"] = unapplied
    state["restored"] = True
    state["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_json(work / STATE, state)
    return state


# --- driving it over ssh ------------------------------------------------------


def receive(stream=sys.stdin) -> None:
    """Take a fresh plan (and this module's newest code) and restore, detached.

    Detached so that a laptop going to sleep mid-restore does not take the
    restore with it -- the box finishes on its own, and `--watch` can pick the
    progress up again from any machine.
    """
    payload = json.load(stream)
    run = run_dir()
    run.mkdir(parents=True, exist_ok=True)
    if payload.get("script"):
        (run / "kdev_box.py").write_text(payload["script"])
    (run / "plan.json").write_text(json.dumps(payload["plan"]))
    log = (run / "restore.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, str(run / "kdev_box.py"), str(run / "plan.json")],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(run),
    )
    (run / "restore.pid").write_text(str(proc.pid))
    print(json.dumps({"started": proc.pid}), flush=True)


def watch(poll: float = 1.0) -> int:
    """Print progress as JSON lines until the restore is done or has died."""
    path = work_dir() / STATE
    last = None
    while True:
        state = _read_json(path)
        line = json.dumps({k: state.get(k) for k in ("restored", "done", "total", "missing")})
        if line != last:
            print(line, flush=True)
            last = line
        if state.get("restored"):
            return 0
        if not _restore_running():
            # One last look: it may have finished between the two reads.
            if _read_json(path).get("restored"):
                continue
            print(json.dumps({"error": "restore is not running"}), flush=True)
            return 1
        time.sleep(poll)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--receive"]:
        receive()
        return 0
    if argv[:1] == ["--watch"]:
        return watch()
    if len(argv) == 1:
        run_dir().mkdir(parents=True, exist_ok=True)
        (run_dir() / "restore.pid").write_text(str(os.getpid()))
        try:
            state = restore(json.loads(Path(argv[0]).read_text()))
        finally:
            (run_dir() / "restore.pid").unlink(missing_ok=True)
        print(json.dumps({k: state.get(k) for k in ("done", "total", "missing")}))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
