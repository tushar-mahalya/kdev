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

import httpx

from . import api, bootstrap, cloudflared, config, persistence, sshcfg, ui
from .errors import KdevError

#: A session that never reports a tunnel is using quota for nothing, so the
#: wait is bounded. Kaggle's own queue can be slow, hence the generous default.
READY_TIMEOUT = 1800

# Include older live runs hidden by a newer cancelled/source-only version.
LIVE_LOOK_BACK = 10
# A stale metadata read must not cause an unbounded walk through notebook history.
MAX_START_VERSIONS = 100
STATUS_TIMEOUT = 60
ENDED_STATES = frozenset({"COMPLETE", "ERROR", "CANCEL_ACKNOWLEDGED"})

_READY = re.compile(rf"{bootstrap.READY_MARKER} host=(\S+)")
_STAGE = re.compile(rf"{bootstrap.STAGE_MARKER} (\w+)")
_SESSION = re.compile(r"KDEV_SESSION id=(\d+)(?: by=(\S*))?")

EDITORS = ("code", "cursor", "code-insiders", "windsurf")


def version_number(value: object) -> int:
    """Accept Kaggle's integer or decimal-string version number, never a guess."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise KdevError("Kaggle did not return a valid notebook version.")
    if isinstance(value, str) and not value.isdecimal():
        raise KdevError("Kaggle did not return a valid notebook version.")
    try:
        number = int(value)
    except ValueError as e:
        raise KdevError("Kaggle did not return a valid notebook version.") from e
    if number < 0:
        raise KdevError("Kaggle did not return a valid notebook version.")
    return number


def current_status(creds: api.Creds, slug: str) -> tuple[str, dict]:
    """The oldest recent live run, or the latest run when all have ended.

    A cancelled race loser becomes the latest version, while the winner keeps
    running in an earlier one. All box commands must discover the same winner.
    """
    latest = version_number(api.get_kernel(creds, slug).get("currentVersionNumber") or 0)
    state = api.session_status(creds, slug, f"v{latest}" if latest else "")
    winner = "", state
    saving: tuple[str, dict] | None = None
    first = max(1, latest - MAX_START_VERSIONS - LIVE_LOOK_BACK + 1)
    for number in range(latest, first - 1, -1):
        label = f"v{number}"
        candidate = state if number == latest else api.session_status(creds, slug, label)
        if candidate.get("status") in api.LIVE_STATES:
            winner = label, candidate
        elif candidate.get("status") == api.SAVING:
            saving = label, candidate
        elif candidate.get("status") in ENDED_STATES:
            files = persistence.version_files(creds, slug, label) or {}
            saved = persistence._state(files)
            if persistence.STATE in files and saved is None:
                raise KdevError(f"Could not read the saved startup state of {label}.")
            saved = saved or {}
            # A confirmed startup had already waited for every older contender.
            # Legacy history has no flag; retain the bounded compatibility walk.
            if saved.get("startup_confirmed") is True or (
                "startup_confirmed" not in saved and latest - number >= LIVE_LOOK_BACK - 1
            ):
                break
    else:
        if first > 1:
            raise KdevError(
                "Too many unconfirmed notebook versions to safely find the running box.",
                f"Inspect the runs at https://www.kaggle.com/code/{slug} before starting another box.",
            )
    if winner[0]:
        return winner
    if saving:
        return saving
    return (f"v{latest}" if latest else ""), state


def start_winner(creds: api.Creds, slug: str, version: int, previous: int) -> str:
    """Elect the lowest live version, including every intervening submission.

    Recheck newly created versions whose status is not visible yet. A failed or
    unknown check never becomes permission to connect to a second box.
    """
    first = max(1, previous - LIVE_LOOK_BACK + 1)
    if version <= previous or version - first > MAX_START_VERSIONS:
        raise KdevError("Cannot safely resolve concurrent starts for this notebook.")
    deadline = time.monotonic() + STATUS_TIMEOUT
    for number in range(first, version):
        label = f"v{number}"
        while True:
            state = api.session_status(creds, slug, label).get("status")
            if state in api.LIVE_STATES:
                return label
            if state in ENDED_STATES or state == api.SAVING or number <= previous:
                break
            if time.monotonic() >= deadline:
                raise KdevError(
                    f"Kaggle has not reported the status of concurrent version {label}."
                )
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    return f"v{version}"


def resolve_start(creds: api.Creds, slug: str, version: int, previous: int) -> str:
    """Stop our duplicate before connecting elsewhere; clean up failed elections."""
    own = f"v{version}"
    try:
        winner = start_winner(creds, slug, version, previous)
    except (api.KaggleError, KdevError, httpx.HTTPError, KeyboardInterrupt):
        cancel_created(creds, slug, own)
        raise
    if winner != own:
        cancel_created(creds, slug, own)
        winner = start_winner(creds, slug, version, previous)
        if winner == own:
            raise KdevError(
                "The other session ended while the duplicate was stopping.",
                "Our duplicate is stopped. Retry kdev up to start a new box.",
            )
    return winner


def rejected_start(creds: api.Creds, slug: str, timeout: int = STATUS_TIMEOUT) -> str:
    """A definitively rejected save can join a concurrently accepted one.

    Kaggle can answer HTTP 200 with a database-conflict error when several
    clients save simultaneously. Do not resubmit: wait for the accepted run.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        version, state = current_status(creds, slug)
        if state.get("status") in api.LIVE_STATES:
            return version
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    return ""


def wait_single(creds: api.Creds, slug: str, version: str, timeout: int = READY_TIMEOUT) -> None:
    """Wait for later racing clients to stop their runs before using shared SSH."""
    own = version_number(version.removeprefix("v"))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if api.session_status(creds, slug, version).get("status") not in api.LIVE_STATES:
            raise KdevError(
                f"Selected session {version} ended before it was ready.", "Retry kdev up."
            )
        latest = version_number(api.get_kernel(creds, slug).get("currentVersionNumber", 0))
        if latest < own or latest - own > MAX_START_VERSIONS:
            raise KdevError("Cannot verify which sessions share this notebook's tunnel.")
        if all(
            api.session_status(creds, slug, f"v{number}").get("status") in ENDED_STATES
            for number in range(own + 1, latest + 1)
        ):
            return
        time.sleep(min(3, max(0, deadline - time.monotonic())))
    raise KdevError(
        "Another start has not finished stopping; the shared tunnel is not safe to use yet.",
        f"Check the runs at https://www.kaggle.com/code/{slug}, then retry kdev up.",
    )


def _session_logs(creds: api.Creds, slug: str, timeout: float, version: str):
    """Follow one version with bounded reads, including a still-queued run."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = deadline - time.monotonic()
        try:
            for line in api.stream_logs(
                creds,
                slug,
                wait_seconds=min(30, max(1, int(left))),
                idle=min(30, left),
                version=version,
            ):
                if time.monotonic() >= deadline:
                    return
                yield line
        except (httpx.HTTPError, api.KaggleError) as e:
            if isinstance(e, api.KaggleError) and e.status not in {0, 404, *api.RETRY_STATUS}:
                raise
        if not version:
            return  # Preserve latest-log callers' existing one-stream behaviour.
        state = api.session_status(creds, slug, version).get("status")
        if state in ENDED_STATES or state == api.SAVING:
            return
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def cancel_created(creds: api.Creds, slug: str, version: str, timeout: int = READY_TIMEOUT) -> None:
    """Cancel only our submitted version, as its starter, and confirm it ended."""
    deadline = time.monotonic() + timeout
    sid = ""
    hint = f"Stop {version} at https://www.kaggle.com/code/{slug}, then retry kdev up."
    try:
        if api.session_status(creds, slug, version).get("status") in ENDED_STATES:
            return
        sid, runner = live_session_id(creds, slug, timeout=timeout, version=version)
        if not sid:
            if api.session_status(creds, slug, version).get("status") in ENDED_STATES:
                return
            raise KdevError(f"Could not find the session id of duplicate {version}.", hint)
        hint = f"Stop it: kdev down --session-id {sid}"
        if runner and runner != creds.username:
            raise KdevError(f"The boot log of {version} names a different starter.", hint)
        try:
            api.cancel_session(creds, int(sid))
        except (api.KaggleError, httpx.HTTPError):
            # The run may have ended between reading its log and cancelling.
            if api.session_status(creds, slug, version).get("status") in ENDED_STATES:
                return
            raise
        while time.monotonic() < deadline:
            if api.session_status(creds, slug, version).get("status") in ENDED_STATES:
                return
            time.sleep(min(3, max(0, deadline - time.monotonic())))
    except (api.KaggleError, httpx.HTTPError) as e:
        raise KdevError(f"Could not confirm that duplicate {version} stopped.", hint) from e
    raise KdevError(f"Duplicate {version} has not finished stopping.", hint)


def ssh_ready(cfg: config.Config) -> None:
    """Make `ssh <alias>` -- and VS Code's host of that name -- work here.

    A named tunnel's hostname never changes, so the block needs no box to look
    at: a machine that did not start the running box, or that ran `kdev down`
    on the last one, can still reach the next.
    """
    if cfg.tunnel_hostname and cfg.ssh_host_alias and not sshcfg.has_block():
        sshcfg.write(cfg.ssh_host_alias, cfg.tunnel_hostname, cloudflared_path=cloudflared.find())


def box_gone(cfg: config.Config) -> None:
    """After a box stops. Only a quick tunnel's block is stale now: its
    hostname dies with the box. A named one is where the next box will be."""
    if not cfg.tunnel_hostname:
        sshcfg.clear()


def reachable(alias: str, timeout: int = 10) -> bool:
    """Can ssh open a channel to the box right now?"""
    if not alias:
        return False
    try:
        r = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", alias, "true"],
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
    *,
    version: str = "",
) -> str | None:
    """Follow the session log until the box announces its tunnel.

    Drives `board` from the stage markers, and records the session id and who
    started it into `seen` as they go past.
    """
    for line in _session_logs(creds, slug, timeout, version):
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
            if (
                version
                and api.session_status(creds, slug, version).get("status") not in api.LIVE_STATES
            ):
                return None
            return ready.group(1)
    return None


#: How long a stopped box may take to save. Kaggle uploads /kaggle/working
#: before it calls the run finished: measured ~110 s for 1 GB.
SAVE_TIMEOUT = 1800

#: find_live_box's answer for a box that was told to stop and is saving.
STOPPING = "stopping"


def wait_saved(
    creds: api.Creds,
    slug: str,
    running_too: bool = False,
    timeout: int = SAVE_TIMEOUT,
    *,
    version: str = "",
) -> None:
    """Block while a session that stopped is still saving its files.

    Planning a restore before then builds on a partial or older version, and
    everything done in the session that stopped is dropped from then on. A
    cancelled box saves as CANCEL_REQUESTED; one told to stop saves while
    still RUNNING, which is `running_too`.
    """
    busy = {api.SAVING, *(api.LIVE_STATES if running_too else ())}
    if running_too:
        busy.add("UNKNOWN")
    deadline = time.monotonic() + timeout
    waiting = False
    while True:
        status = api.session_status(creds, slug, version).get("status", "UNKNOWN")
        if status not in busy and not (waiting and status == "UNKNOWN"):
            return
        waiting = True
        if time.monotonic() > deadline:
            raise KdevError(
                "The last session is still saving its files.",
                "Starting now would leave them out. Try again in a few minutes.",
            )
        time.sleep(3)


def stopping(creds: api.Creds, slug: str, window: float = 20, *, version: str = "") -> bool:
    """Whether the running box was already told to stop, and is saving.

    After `kdev down` Kaggle keeps the run RUNNING for as long as the upload
    takes -- minutes, for a few GB -- with the tunnel already gone: from
    outside, just like a live box that cannot be reached. Its log says which.
    """
    deadline = time.time() + window
    try:
        # A box told to stop logs it within 5 s; `idle` outlasts that.
        for line in api.stream_logs(creds, slug, wait_seconds=30, idle=8, version=version):
            if "KDEV_STOP" in line or "KDEV_DONE" in line:
                return True
            if time.time() > deadline:
                break
    except api.KaggleError:
        pass
    return False


def find_live_box(cfg: config.Config, creds: api.Creds, target: str, *, version: str = "") -> str:
    """Hostname of a kdev box running on `target`, STOPPING if it is on its
    way out, or "" if there is none.

    The session status alone is not enough: a Quick Save reads as running for a
    few seconds, and so does a session started in the Kaggle editor. A kdev box
    is one that announced its tunnel -- and one still booting will do so
    shortly, so follow its log rather than guess.
    """
    if cfg.tunnel_hostname and reachable(cfg.ssh_host_alias):
        return cfg.tunnel_hostname
    if stopping(creds, target, version=version):
        return STOPPING
    return await_ready(creds, target, timeout=900, version=version) or ""


def live_session_id(
    creds: api.Creds, slug: str, timeout: int = 60, *, version: str = ""
) -> tuple[str, str]:
    """(session id, username that started it), from the box's boot line."""
    try:
        for line in _session_logs(creds, slug, timeout, version):
            found = _SESSION.search(line)
            if found:
                return found.group(1), found.group(2) or ""
            if bootstrap.READY_MARKER in line:
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
    *,
    session_id: str = "",
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

    ok, last = persistence.restore_on_box(alias, plan, progress, session_id=session_id)
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
    cfg: config.Config,
    creds: api.Creds,
    target: str,
    hours: float,
    gpu: str,
    account: str,
    *,
    version: str = "",
) -> tuple[float, str, str]:
    """(hours left, accelerator, started by) for a box someone else may have
    started: its own record, not this machine's defaults."""
    state = persistence.box_state(cfg.ssh_host_alias)
    if state.get("ends"):
        hours = max(0.0, (float(state["ends"]) - time.time()) / 3600)
    account = state.get("run_by") or account
    try:
        shape = api.get_kernel(creds, target, version).get("machineShape") or ""
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
