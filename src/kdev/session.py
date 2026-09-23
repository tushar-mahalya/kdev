"""A live session: finding it, reaching it, restoring into it, stopping it.

Pure orchestration over the Kaggle API, ssh and the on-box restore; the
commands in `kdev.commands` decide what to show around it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable

from . import api, bootstrap, config, persistence, ui

#: A session that never reports a tunnel is using quota for nothing, so the
#: wait is bounded. Kaggle's own queue can be slow, hence the generous default.
READY_TIMEOUT = 1800

_READY = re.compile(rf"{bootstrap.READY_MARKER} host=(\S+)")
_STAGE = re.compile(rf"{bootstrap.STAGE_MARKER} (\w+)")
_SESSION = re.compile(r"KDEV_SESSION id=(\d+)(?: by=(\S*))?")

EDITORS = ("code", "cursor", "code-insiders", "windsurf")


def reachable(alias: str, timeout: int = 10) -> bool:
    """Can ssh open a channel to the box right now?"""
    if not alias:
        return False
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", alias, "true"],
            capture_output=True,
            timeout=timeout + 15,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return r.returncode == 0


def await_ready(
    creds: api.Creds,
    slug: str,
    board: ui.Stages | None = None,
    timeout: int = READY_TIMEOUT,
    seen: dict | None = None,
) -> str | None:
    """Follow the session log until the box announces its tunnel.

    Drives `board` from the stage markers, and records the session id and who
    started it into `seen` as they go past.
    """
    deadline = time.time() + timeout
    for line in api.stream_logs(creds, slug):
        if time.time() > deadline:
            if board:
                board.note(f"gave up after {timeout // 60} min without a tunnel")
            return None
        if not line.strip():
            continue
        found = _SESSION.search(line)
        if found and seen is not None:
            seen["session"], seen["by"] = found.group(1), found.group(2) or ""
        if board:
            stage = _STAGE.search(line)
            if stage:
                board.advance(stage.group(1))
            else:
                board.note(line.strip())
        ready = _READY.search(line)
        if ready:
            return ready.group(1)
    return None


def find_live_box(cfg: config.Config, creds: api.Creds, target: str) -> str:
    """Hostname of a kdev box running on `target`, or "" if there is none.

    The session status alone is not enough: a Quick Save reads as running for a
    few seconds, and so does a session started in the Kaggle editor. A kdev box
    is one that announced its tunnel -- and one still booting will do so
    shortly, so follow its log rather than guess.
    """
    if cfg.tunnel_hostname and reachable(cfg.ssh_host_alias):
        return cfg.tunnel_hostname
    return await_ready(creds, target, timeout=900) or ""


def live_session_id(creds: api.Creds, slug: str, timeout: int = 60) -> tuple[str, str]:
    """(session id, username that started it), from the box's boot line."""
    deadline = time.time() + timeout
    try:
        for line in api.stream_logs(creds, slug, wait_seconds=30):
            found = _SESSION.search(line)
            if found:
                return found.group(1), found.group(2) or ""
            if bootstrap.READY_MARKER in line or time.time() > deadline:
                break
    except api.KaggleError:
        pass
    return "", ""


def cancel_as_owner(cfg: config.Config, sid: int, runner: str = "") -> str:
    """Cancel a session as the account that started it. Returns that account.

    Kaggle refuses a cancel from anyone else -- measured: 403
    `kernelSessions.cancel` even for an Editor of the notebook. So try the
    known starter first, then every signed-in account (a box that predates
    the `by=` field has no name to go on).
    """
    names = sorted(cfg.profiles, key=lambda n: cfg.profiles[n].username != runner)
    for name in names:
        try:
            api.cancel_session(cfg.profile(name).creds, sid)
        except api.KaggleError:
            continue
        return name
    return ""


def restore(
    creds: api.Creds,
    notebook: str,
    alias: str,
    layers: list[str],
    note: Callable[[str], None] | None = None,
) -> dict:
    """Resolve fresh URLs for `layers` and have the box restore itself.

    Never raises for a restore that did not finish: the box is up either way,
    and the unfinished state is recorded on it for `kdev restore` or the next
    `kdev up` to complete.
    """
    say = note or (lambda _text: None)
    say("resolving saved files…")
    plan = persistence.build_plan(creds, notebook, layers)
    if not sum(len(layer["files"]) for layer in plan["layers"]):
        say("saved version is empty")
        return {"restored": True, "done": 0, "total": 0}

    def progress(state: dict) -> None:
        if state.get("total"):
            say(f"{state.get('done', 0)}/{state['total']} files")

    ok, last = persistence.restore_on_box(alias, plan, progress)
    if not ok:
        say(last.get("error") or "restore did not finish")
    return {**last, "restored": ok}


def report_restore(state: dict) -> None:
    if not state:
        return
    if not state.get("restored"):
        ui.warn(
            "Not all your files are back yet: "
            + (state.get("error") or "the restore did not finish"),
            "Finish it with `kdev restore`; it never overwrites work from this session.",
        )
        return
    if state.get("total"):
        ui.ok(f"restored {state.get('done', 0)} file(s) into /kaggle/working")
    elif "total" in state:
        ui.ok("nothing missing: every saved file is already on the box")
    missing = state.get("missing") or []
    for name in missing[:10]:
        ui.warn(f"could not restore {name}")
    if len(missing) > 10:
        ui.warn(f"… and {len(missing) - 10} more")
    if missing:
        ui.hint("Retry with `kdev restore`.")


def describe_running(
    cfg: config.Config, creds: api.Creds, target: str, hours: float, gpu: str, account: str
) -> tuple[float, str, str]:
    """(hours left, accelerator, started by) for a box someone else may have
    started: its own record, not this machine's defaults."""
    state = persistence.box_state(cfg.ssh_host_alias)
    if state.get("ends"):
        hours = max(0.0, (float(state["ends"]) - time.time()) / 3600)
    account = state.get("run_by") or account
    try:
        shape = api.get_kernel(creds, target).get("machineShape") or ""
        gpu = next((k for k, v in api.SHAPES.items() if v == shape), "none")
    except api.KaggleError:
        pass
    return round(hours, 1), ui.gpu_label(gpu), account


def editor() -> str:
    """The first VS Code-compatible CLI on PATH, or ""."""
    return next((exe for exe in EDITORS if shutil.which(exe)), "")


def open_editor(alias: str, remote_path: str = "/kaggle/working") -> bool:
    exe = editor()
    uri = f"vscode-remote://ssh-remote+{alias}{remote_path}"
    if not exe:
        ui.hint(f"No VS Code CLI on PATH. Open this in VS Code: {uri}")
        return False
    subprocess.Popen(
        [exe, "--folder-uri", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    ui.ok(f"opened {exe} on {alias}")
    return True


def open_shell(alias: str) -> int:
    return subprocess.run(["ssh", alias]).returncode
