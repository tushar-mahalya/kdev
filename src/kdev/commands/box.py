"""The box: start it, reach it, see it, stop it, and get your files back."""

from __future__ import annotations

import re
import subprocess
import time

import typer
from rich.text import Text

from .. import (
    api,
    bootstrap,
    cloudflared,
    config,
    persistence,
    quota,
    safety,
    session,
    sshcfg,
    ui,
)
from .. import notebook as nb
from ..errors import KdevError
from ._common import cap_for, context, load_ready, need_notebook

OPEN_CHOICES = ("code", "shell", "none")


# --- up -----------------------------------------------------------------------


def up(
    gpu: str | None = typer.Option(None, "--gpu", help=f"Accelerator: {', '.join(api.SHAPES)}."),
    hours: float | None = typer.Option(
        None, "--hours", help="Session length (Kaggle stops it at 12h, 9h on TPU)."
    ),
    account: str = typer.Option("", "--account", "-a", help="Run it on this account."),
    open_with: str | None = typer.Option(None, "--open", help="When ready: code, shell or none."),
    restore: bool = typer.Option(True, "--restore/--no-restore", help="Put your saved files back."),
    replace: bool = typer.Option(False, "--replace", help="Replace a box that is already running."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No questions: use flags and defaults."),
) -> None:
    """Start the box, or connect to the one already running."""
    interactive = ui.interactive() and not yes
    if open_with is not None and open_with not in OPEN_CHOICES:
        raise KdevError(f"--open takes one of: {', '.join(OPEN_CHOICES)}.")
    cfg = load_ready(interactive)
    target = need_notebook(cfg)
    if not cfg.ssh_public_key:
        raise KdevError("No SSH key configured on this machine.", "Run: kdev setup")

    gpu, hours, gpu_picked, hours_picked = _box_spec(cfg, gpu, hours, interactive)
    name, prof = quota.pick_account(cfg, account, hours, gpu, interactive)
    ui.header("up", context(cfg, name, target))
    cf = _cloudflared(interactive)

    # A box already running is the one you left: from this account or another,
    # this machine or another. Connect to it rather than start a second.
    if api.session_status(prof.creds, target).get("status") in api.LIVE_STATES:
        live = ""
        if not replace:
            with ui.spinner("a session is running; checking it is your box…"):
                live = session.find_live_box(cfg, prof.creds, target)
        if live == session.STOPPING:
            # `kdev down` a moment ago: wait for its save, then start afresh.
            # Connecting would find no tunnel; planning now would miss the save.
            with ui.spinner("your last box is still saving your files…"):
                session.wait_saved(prof.creds, target, running_too=True)
            ui.ok("the last box finished saving")
            must_stop = False
        elif live:
            choice = _running_choice(target, interactive)
            if choice == "cancel":
                return
            if choice == "connect":
                _connect(cfg, prof, target, live, cf, hours, gpu, name, open_with, interactive)
                return
            must_stop = True  # "replace" was chosen
        elif api.session_status(prof.creds, target).get("status") in api.LIVE_STATES:
            if not replace:
                ui.warn(f"{target} has a session kdev cannot reach (started in the Kaggle editor?)")
                if not (interactive and ui.confirm("Replace it with a kdev box?", default=False)):
                    raise KdevError("Left it running.", "Stop it with `kdev down`, then `kdev up`.")
            must_stop = True
        else:
            # It ended while we were looking (finding a box can take minutes):
            # nothing is left to stop, so just start.
            must_stop = False
        # Replacing: stop it and wait for its save *before* planning the
        # restore. The running version has no saved output yet, so planning now
        # would build on the one before it and drop everything done since.
        if must_stop:
            _stop_and_wait(cfg, prof.creds, target)

    with ui.spinner("reading the notebook…"):
        verdict, existing, backup = safety.check(prof.creds, target)
    if not (cfg.tunnel_credentials or cfg.tunnel_token) and nb.adopt_config(cfg, prof.creds):
        config.save(cfg)
        ui.ok(f"using this workspace's tunnel, {cfg.tunnel_hostname or 'token'}")

    # Planned even with --no-restore: the box must record what it *should*
    # hold, or a deliberately empty session would look complete and every
    # later `kdev up` would build on it alone, dropping the history.
    with ui.spinner("finding where you left off…"):
        session.wait_saved(prof.creds, target)
        try:
            meta = api.get_kernel(prof.creds, target)
        except api.KaggleError:
            meta = {}
        try:
            latest = int(meta.get("currentVersionNumber") or 0)
        except (TypeError, ValueError):
            latest = 0
        layers = persistence.plan_layers(prof.creds, target, latest)
    # SaveKernel sets the title it is given: keep the notebook's own, or a
    # notebook made in the Kaggle editor ("v0.1.0_testing") gets renamed to
    # its slug ("v0-1-0-testing").
    title = meta.get("title") or target.split("/")[-1]
    if layers and not restore:
        ui.warn(f"starting empty; {' + '.join(layers)} stays saved for next time")

    if hours > 4 and not cfg.git_remote:
        ui.hint(
            f"Tip: a git remote pushes every 15 min, so even a lost machine costs "
            f"minutes, not {hours:g}h. kdev config set git-remote <url>"
        )

    hold = int(hours * 3600)
    script = bootstrap.render(
        ssh_public_key=cfg.ssh_public_key,
        tunnel_token=cfg.tunnel_token,
        tunnel_credentials=cfg.tunnel_credentials,
        tunnel_id=cfg.tunnel_id,
        tunnel_hostname=cfg.tunnel_hostname,
        # Stop ~2 min before Kaggle does, so the teardown push and the saved
        # version both land.
        hold_seconds=max(hold - 120, 60),
        run_by=prof.username,
        git_remote=cfg.git_remote,
        git_deploy_key=cfg.git_deploy_key,
        restore_layers=layers,
        notebook=target,
    )
    source, kernel_type = bootstrap.inject(existing, script)
    if verdict == "occupied":
        ui.hint(f"your code in the notebook is kept below kdev's cell (backup: {backup})")

    label = ui.gpu_label(gpu)
    stages = [
        ("submit", "Submitting"),
        ("queue", "Waiting for a machine"),
        *bootstrap.STAGES,
        ("restore", "Restoring your files"),
        ("ready", "Ready"),
    ]
    host, reachable = None, False
    seen: dict = {}
    restored: dict = {}
    with ui.Stages(stages, head=f"{name} · {label} · {hours:g}h") as board:
        board.advance("submit")
        resp = api.save_kernel(
            prof.creds,
            slug=target,
            title=title,
            source=source,
            machine_shape=api.SHAPES[gpu],
            timeout_seconds=hold,
            kernel_type=kernel_type,
        )
        board.note(f"version {resp.get('versionNumber')}")
        board.advance("queue")
        host = session.await_ready(prof.creds, target, board, seen=seen)
        if host:
            sshcfg.write(cfg.ssh_host_alias, host, cloudflared_path=cf)
            board.advance("restore")
            if layers and restore:
                restored = session.restore(
                    prof.creds, target, cfg.ssh_host_alias, layers, board.note
                )
            else:
                board.note("nothing saved yet" if not layers else "skipped (--no-restore)")
            board.advance("ready")
            # The log says the tunnel is up a moment before Cloudflare's edge
            # routes it; ready has to mean an ssh would work right now.
            board.note("waiting for ssh…")
            reachable = persistence.wait_reachable(cfg.ssh_host_alias)
            if reachable:
                board.note("")
                board.finish()
            else:
                board.fail("the tunnel is up but ssh does not answer")
        else:
            board.fail("no tunnel was announced")

    if not host:
        _explain_no_tunnel(prof.creds, target, seen)
    # Only what was answered at a prompt is remembered, field by field: an
    # explicit --gpu for this run must not ride along into the defaults just
    # because the session length happened to be asked for.
    if gpu_picked:
        cfg.default_gpu = gpu
    if hours_picked:
        cfg.default_hours = hours
    if gpu_picked or hours_picked:
        config.save(cfg)
    session.report_restore(restored)
    if not reachable:
        raise KdevError(
            f"The box is running at {host}, but ssh does not answer yet.",
            "Try again in a minute: kdev ssh    See what it is doing: kdev logs",
        )

    ui.blank()
    files = f"{restored.get('done', 0)} restored" if restored.get("total") else ""
    ui.ready_card(cfg.ssh_host_alias, host, hours, label, name, note=files)
    _open(cfg.ssh_host_alias, open_with, interactive)


def _box_spec(
    cfg: config.Config, gpu: str | None, hours: float | None, interactive: bool
) -> tuple[str, float, bool, bool]:
    """Flag, then the saved default, then ask. Returns (gpu, hours, and
    whether each was answered at a prompt). Only prompted answers become
    defaults: `kdev up --gpu none` for a quick CPU job must not quietly make
    every later `kdev up` a CPU box."""
    gpu_picked = hours_picked = False
    if gpu is None:
        if cfg.default_gpu:
            gpu = cfg.default_gpu
        elif interactive:
            gpu, gpu_picked = ui.pick_gpu(), True
        else:
            gpu = "t4"
    if gpu not in api.SHAPES:
        raise KdevError(f"--gpu must be one of: {', '.join(api.SHAPES)}.")
    cap = cap_for(gpu)
    if hours is None:
        if cfg.default_hours:
            hours = cfg.default_hours
        elif interactive:
            hours, hours_picked = ui.pick_hours(gpu, cap), True
        else:
            hours = 9.0
    if not hours or hours <= 0:
        raise KdevError("--hours must be more than zero.")
    if hours > cap:
        ui.warn(f"Kaggle stops {ui.gpu_label(gpu)} sessions at {cap:g}h; using {cap:g}h")
        hours = cap
    return gpu, float(hours), gpu_picked, hours_picked


def _cloudflared(interactive: bool):
    """Resolved before the session starts: discovering it is missing after the
    container boots would waste quota on a box nobody can reach."""
    cf = cloudflared.find()
    if cf:
        return cf
    if interactive and not ui.confirm("cloudflared is missing (ssh needs it). Download it now?"):
        raise KdevError(
            "kdev needs cloudflared to reach the box.",
            "Install it: brew install cloudflared   or: kdev doctor --fix",
        )
    cf = ui.download("cloudflared", cloudflared.install)
    ui.ok(f"cloudflared {cloudflared.version(cf)}")
    return cf


def _stop_and_wait(
    cfg: config.Config, creds: api.Creds, target: str, timeout: int = session.SAVE_TIMEOUT
) -> None:
    with ui.spinner("stopping the running box and waiting for it to save…"):
        stopped = False
        if session.reachable(cfg.ssh_host_alias):
            r = subprocess.run(
                [
                    "ssh",
                    "-T",
                    "-o",
                    "BatchMode=yes",
                    cfg.ssh_host_alias,
                    "touch /kaggle/working/.kdev-stop",
                ],
                capture_output=True,
            )
            stopped = r.returncode == 0
        if not stopped:
            sid, runner = session.live_session_id(creds, target)
            if not sid or not session.cancel_as_owner(cfg, int(sid), runner):
                raise KdevError(
                    "Could not stop the running box from here.",
                    f"Stop it at {nb.notebook_url(target)}, then run kdev up again.",
                )
        deadline = time.time() + timeout
        while api.session_status(creds, target).get("status") in api.LIVE_STATES:
            if time.time() > deadline:
                raise KdevError(
                    "The old box is taking too long to stop.",
                    "Check it with kdev status, then run kdev up again.",
                )
            time.sleep(5)
    ui.ok("stopped the old box; its files are saved")


def _running_choice(target: str, interactive: bool) -> str:
    """connect, replace or cancel, for a box that is already up."""
    ui.ok(f"{target} is already running")
    if not interactive:
        return "connect"
    return ui.choose(
        "It is your box. What now?",
        [
            ("connect", "Connect to it                 (recommended)"),
            ("replace", "Stop it and start a new one"),
            ("cancel", "Nothing"),
        ],
    )


def _connect(cfg, prof, target, host, cf, hours, gpu, account, open_with, interactive) -> None:
    sshcfg.write(cfg.ssh_host_alias, host, cloudflared_path=cf)
    with ui.spinner("waiting for ssh…"):
        ok = persistence.wait_reachable(cfg.ssh_host_alias, timeout=60)
    if not ok:
        raise KdevError(
            "The box is running but ssh does not answer.",
            "If this laptop has never started this workspace, its key is not on "
            "the box yet; it is added the first time it runs `kdev up`.",
        )
    state = persistence.box_state(cfg.ssh_host_alias)
    if state and not state.get("restored"):
        ui.warn("That session never finished restoring your files", "Finish it: kdev restore")
    hours, label, account = session.describe_running(cfg, prof.creds, target, hours, gpu, account)
    ui.blank()
    ui.ready_card(cfg.ssh_host_alias, host, hours, label, account)
    _open(cfg.ssh_host_alias, open_with, interactive)


def _open(alias: str, how: str | None, interactive: bool) -> None:
    """Open the box the way you want to work in it."""
    if how is None:
        if not interactive:
            return
        editor = session.editor()
        options = [("code", f"Open in {editor}    (recommended)")] if editor else []
        options += [("shell", "Open a shell here"), ("none", "Leave it running")]
        how = ui.choose("Open it?", options)
    if how == "code":
        session.open_editor(alias)
    elif how == "shell":
        session.open_shell(alias)
        ui.hint("Back on your machine. The box is still running: kdev down stops it.")


def _explain_no_tunnel(creds: api.Creds, target: str, seen: dict) -> None:
    try:
        state = api.session_status(creds, target)
    except api.KaggleError:
        state = {}
    hint = "See what it did: kdev logs"
    if state.get("status") in api.LIVE_STATES:
        ui.warn(f"It is still {state['status'].lower()} and still using quota")
        hint = (
            f"Stop it: kdev down --session-id {seen['session']}"
            if seen.get("session")
            else f"Stop it at {nb.notebook_url(target)}"
        )
    if state.get("failureMessage"):
        ui.hint(state["failureMessage"])
    raise KdevError("The session never reported a tunnel.", hint)


# --- down ---------------------------------------------------------------------


def down(
    session_id: int = typer.Option(0, "--session-id", help="Cancel this session id directly."),
    account: str = typer.Option("", "--account", "-a", help="Cancel as this account."),
) -> None:
    """Stop the box. Your files are saved however it stops."""
    cfg = config.load()
    session.ssh_ready(cfg)

    if session_id:
        if account:
            api.cancel_session(cfg.profile(account).creds, session_id)
            by = account
        else:
            by = session.cancel_as_owner(cfg, session_id)
        if not by:
            raise KdevError(
                f"None of the signed-in accounts may cancel {session_id}.",
                "Kaggle only lets the account that started it do that.",
            )
        session.box_gone(cfg)
        ui.ok(f"cancelled session {session_id} as {by}; Kaggle saves its files as it stops")
        return

    alias = cfg.ssh_host_alias
    try:
        r = subprocess.run(
            [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                alias,
                "touch /kaggle/working/.kdev-stop",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        reached = r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        reached = False
    if reached:
        session.box_gone(cfg)
        ui.ok(
            "stopping; the box saves /kaggle/working as it exits",
            "Next `kdev up` brings it all back, on any account or machine.",
        )
        return

    # It does not answer. It may be gone, or unreachable while still using
    # quota: find out, and cancel it as whoever started it.
    target = need_notebook(cfg)
    creds = cfg.profile(account).creds
    status = api.session_status(creds, target).get("status", "?")
    if status not in api.LIVE_STATES:
        session.box_gone(cfg)
        ui.ok(f"nothing running ({status.lower()}); your files are in the saved version")
        return
    if session.stopping(creds, target):
        session.box_gone(cfg)
        ui.ok(
            "already stopping; Kaggle is saving /kaggle/working",
            "Next `kdev up` waits for the save, then brings it all back.",
        )
        return
    with ui.spinner("the box does not answer; finding its session id…"):
        sid, runner = session.live_session_id(creds, target)
    if not sid:
        raise KdevError(
            "The box is still running but does not answer, and its id is not in the log.",
            f"Stop it at {nb.notebook_url(target)} (files are saved either way)",
        )
    by = session.cancel_as_owner(cfg, int(sid), runner)
    if not by:
        raise KdevError(
            f"Only {runner or 'the account that started it'} can cancel session "
            f"{sid}, and it is not signed in here.",
            f"Sign it in (kdev account add) or stop it at {nb.notebook_url(target)}",
        )
    session.box_gone(cfg)
    ui.ok(f"cancelled session {sid} as {by}; Kaggle saves its files as it stops")


# --- ssh ----------------------------------------------------------------------


def ssh(ctx: typer.Context) -> None:
    """Open a shell on the box. Anything after -- goes to ssh."""
    cfg = config.load()
    if not cfg.ssh_host_alias:
        raise KdevError("No ssh alias configured.", "Run: kdev setup")
    session.ssh_ready(cfg)
    if not sshcfg.has_block():
        raise KdevError("No box hostname known yet.", "Start or find it with: kdev up")
    # ssh's own exit code is this command's, so scripts can wrap it.
    raise typer.Exit(subprocess.run(["ssh", cfg.ssh_host_alias, *ctx.args]).returncode)


# --- status -------------------------------------------------------------------


def status(
    account: str = typer.Option("", "--account", "-a", help="Check as this account."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead."),
) -> None:
    """The box: running or not, who started it, when it ends, your files."""
    cfg = config.load()
    session.ssh_ready(cfg)
    target = need_notebook(cfg)
    creds = cfg.profile(account).creds
    with ui.spinner("checking…"):
        try:
            state = api.session_status(creds, target)
        except api.KaggleError as e:
            state = {"status": "UNKNOWN", "failureMessage": str(e)}
        reachable = session.reachable(cfg.ssh_host_alias)
        box = persistence.box_state(cfg.ssh_host_alias) if reachable else {}
        running = state.get("status") in api.LIVE_STATES
        saving = running and not reachable and session.stopping(creds, target)
    info = {
        "notebook": target,
        "status": state.get("status", "?"),
        "running": running,
        "stopping": saving,
        "reachable": reachable,
        "host": cfg.tunnel_hostname or None,
        "started_by": box.get("run_by") or None,
        "session": box.get("session") or None,
        "ends": box.get("ends") or None,
        "restored": box.get("restored") if box else None,
        "restored_from": box.get("layers") if box else None,
        "failure": state.get("failureMessage") or None,
    }
    if as_json:
        ui.emit_json(info)
        return

    # Kaggle's words describe the last *run*; the user asked about the *box*.
    word = {"RUNNING": "running", "QUEUED": "starting"}.get(info["status"], "stopped")
    if saving:
        word = "stopping"
    title = Text.assemble(
        (
            ui.g("live") + " " if running else ui.g("pending") + " ",
            "kdev.ok" if running else "kdev.muted",
        ),
        (word, "bold"),
        ("  " + target, "kdev.muted"),
    )
    rows: list[tuple[str, object]] = []
    if not running:
        last = {
            "COMPLETE": "finished",
            "ERROR": "ended with an error",
            "CANCEL_ACKNOWLEDGED": "was cancelled",
            "CANCEL_REQUESTED": "is being cancelled",
            api.NEVER_RUN: "never run yet",
        }
        rows.append(("last run", last.get(info["status"], info["status"].lower())))
        if info["status"] == api.NEVER_RUN:
            rows.append(("files", "nothing saved yet; `kdev up` starts it"))
        else:
            rows.append(("files", "saved in the notebook; `kdev up` puts them back"))
    if box.get("run_by"):
        rows.append(("started by", box["run_by"]))
    if box.get("ends"):
        left = max(0, int(box["ends"]) - int(time.time()))
        ends = time.strftime("%H:%M", time.localtime(int(box["ends"])))
        rows.append(("ends", f"{ends}  ({ui.fmt_hours(left)} left)"))
    if saving:
        rows.append(("files", "being saved; `kdev up` waits for them"))
    elif running:
        reach = Text(
            f"{cfg.ssh_host_alias} {ui.g('arrow')} {cfg.tunnel_hostname or 'quick tunnel'}  "
        )
        reach.append(
            "reachable" if reachable else "not reachable yet",
            style="kdev.ok" if reachable else "kdev.warn",
        )
        rows.append(("ssh", reach))
    if box:
        layers = " + ".join(box.get("layers") or []) or "nothing saved yet"
        rows.append(
            (
                "files",
                Text(f"restored from {layers}", style="kdev.ok")
                if box.get("restored")
                else Text("restore not finished: kdev restore", style="kdev.warn"),
            )
        )
    if box.get("notebook") and box["notebook"] != target:
        rows.append(
            ("note", Text(f"the running box is {box['notebook']}, not {target}", style="kdev.warn"))
        )
    if info["failure"]:
        rows.append(("error", Text(info["failure"], style="kdev.err")))
    ui.card(title, rows, tone="ok" if running else "muted")
    if running and not reachable and not saving:
        ui.hint("Running but not reachable yet: give it a minute, or `kdev logs` to see why.")


# --- history ------------------------------------------------------------------


def _history_time(value: object) -> str:
    if value in (None, ""):
        return "—"
    if isinstance(value, (int, float)):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))
    return str(value).replace("T", " ").removesuffix("Z")


def history(
    limit: int = typer.Option(10, "--limit", "-n", min=1, help="Show at most N saved sessions."),
    account: str = typer.Option("", "--account", "-a", help="Read as this account."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead."),
) -> None:
    """Past saved sessions: who ran them, how they ended and what they saved."""
    cfg = config.load()
    target = need_notebook(cfg)
    creds = cfg.profile(account).creds
    with ui.spinner("reading session history…"):
        try:
            latest = int(api.get_kernel(creds, target).get("currentVersionNumber") or 0)
            rows = persistence.session_history(creds, target, latest, limit)
        except api.KaggleError as e:
            raise KdevError(f"Could not read the history of {target}.", str(e)) from e

    if as_json:
        ui.emit_json({"notebook": target, "sessions": rows})
        return

    if not rows:
        ui.hint("no saved sessions yet")
        return

    display = []
    for row in rows:
        restored = row["restored"]
        restore = "yes" if restored is True else "no" if restored is False else "—"
        display.append(
            (
                row["version"],
                row["run_by"] or "—",
                _history_time(row["started"]),
                _history_time(row["ends"]),
                row["status"],
                row["files"],
                restore,
            )
        )
    ui.console.print(
        ui.table(
            [
                ("version", "left"),
                ("account", "left"),
                ("started", "left"),
                ("ends", "left"),
                ("status", "left"),
                ("files", "right"),
                ("restored", "left"),
            ],
            display,
            title=target,
        )
    )


# --- logs ---------------------------------------------------------------------


def logs(
    account: str = typer.Option("", "--account", "-a", help="Read as this account."),
    lines: int = typer.Option(0, "--lines", "-n", help="Stop after N lines (default: follow)."),
    everything: bool = typer.Option(False, "--all", help="Include kdev's heartbeat lines."),
) -> None:
    """Stream the session's log."""
    cfg = config.load()
    target = need_notebook(cfg)
    shown = 0
    for line in api.stream_logs(cfg.profile(account).creds, target):
        text = line.rstrip()
        if not everything and text.startswith("KDEV_ALIVE"):
            continue
        ui.console.print(text, highlight=False, markup=False)
        shown += 1
        if lines and shown >= lines:
            return


# --- restore ------------------------------------------------------------------


def restore_cmd(
    account: str = typer.Option("", "--account", "-a", help="Read the notebook as this account."),
    from_version: str = typer.Option(
        "", "--from", help="Bring back files from this saved version, e.g. v12."
    ),
    from_backup: bool = typer.Option(
        False, "--from-backup", help="Push this machine's backup instead."
    ),
) -> None:
    """Put your saved files back into the running box.

    `kdev up` already does this. Run it to finish a restore that was cut
    short, bring back files that an older version still has, or push a local
    backup. It only adds what is missing: a file on the box now is never
    overwritten, so this is not a rollback.
    """
    cfg = config.load()
    session.ssh_ready(cfg)
    alias = cfg.ssh_host_alias
    if from_backup:
        files = persistence.contents()
        if not files:
            raise KdevError("This machine has no backup yet.", "Make one with: kdev backup")
        with ui.spinner("pushing the backup…"):
            ok, err = persistence.push(alias)
        if not ok:
            raise KdevError(
                f"Could not push the backup: {err or 'ssh failed'}", "Is the box up? kdev status"
            )
        ui.ok(f"pushed {len(files)} file(s) into /kaggle/working")
        return

    if from_version and not re.fullmatch(r"v\d+", from_version):
        raise KdevError("--from takes a version like v12.", "See them: kdev workspace files")
    creds = cfg.profile(account).creds
    with ui.spinner("reading the box…"):
        state = persistence.box_state(alias)
    if not state:
        raise KdevError("Could not read the box over ssh.", "Is it up? kdev status")
    # Version labels only mean something for the notebook that saved them: use
    # the box's own, not whatever this machine is pointed at now.
    target = state.get("notebook") or need_notebook(cfg)
    if target != cfg.notebook:
        ui.warn(f"the running box belongs to {target}; restoring from that notebook")
    if from_version and not persistence.version_files(creds, target, from_version):
        raise KdevError(
            f"{from_version} of {target} has no saved files.",
            "A running box's own version has none yet. See one: kdev workspace files --version vN",
        )
    layers = [from_version] if from_version else list(state.get("layers") or [])
    if not layers:
        with ui.spinner("finding where you left off…"):
            latest = int(api.get_kernel(creds, target).get("currentVersionNumber") or 0)
            # The running session is the newest version; build on the one before.
            layers = persistence.plan_layers(creds, target, latest - 1)
    if not layers:
        ui.ok("nothing saved yet, so nothing to restore")
        return
    if state.get("restored") and not from_version:
        ui.hint("already restored; filling in anything that is missing")
    ui.ok(f"restoring from {' + '.join(layers)}")
    with ui.spinner("restoring…") as spin:
        result = session.restore(
            creds,
            target,
            alias,
            layers,
            lambda text: spin.update(Text(f"restoring… {text}", style="kdev.muted")),
        )
    session.report_restore(result)
    if not result.get("restored"):
        raise typer.Exit(1)


# --- backup -------------------------------------------------------------------


def backup() -> None:
    """Copy the running box's /kaggle/working to this machine.

    Optional: Kaggle keeps your files anyway. This is a local copy you can
    open in Finder, and push back with `kdev restore --from-backup`.
    """
    cfg = config.load()
    session.ssh_ready(cfg)
    with ui.spinner("copying the box to this machine…"):
        ok, err = persistence.pull(cfg.ssh_host_alias)
    if not ok:
        raise KdevError(f"Could not reach the box over ssh. {err}".strip(), "Is it up? kdev status")
    ui.ok(f"{len(persistence.contents())} file(s) in {persistence.mirror_dir()}")
