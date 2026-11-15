"""Bare `kdev`: the front door. Everything at a glance, then one key to act.

In a terminal it is a live menu (see `kdev.menu`): the box and every
account's quota load while it is already on screen, each item has a key,
and the highlighted one shows the command it runs. Read-only picks come back
to the menu; picks that hand you the box (up, ssh, VS Code) leave it.
Without a terminal it prints the same facts as plain lines.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from io import StringIO

import typer
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from .. import api, auth, config, menu, persistence, quota, session, tunnel, ui, wizard
from ..errors import Cancelled, KdevError
from ._common import box_label

#: Terminal height from which the home screen opens with the full logo.
LOGO_ROWS = 30


def setup() -> None:
    """Get this machine ready. Safe to re-run: only does what is missing."""
    session.ssh_ready(wizard.run())


# --- facts -------------------------------------------------------------------


def _box_facts(cfg: config.Config) -> dict:
    """The box as Kaggle sees it: one call chain, no ssh."""
    if not cfg.notebook:
        return {"status": "", "running": False}
    _, state = session.current_status(cfg.profile().creds, cfg.notebook)
    status = state.get("status", "")
    return {
        "status": status,
        "running": status in api.LIVE_STATES,
        "saving": status == api.SAVING,
        "queued": status == "QUEUED",
        "failure": state.get("failureMessage") or "",
    }


def _reach_facts(cfg: config.Config) -> dict:
    """The box as ssh sees it: reachable, and its own record of when it ends."""
    if not session.reachable(cfg.ssh_host_alias, timeout=8):
        return {"reachable": False}
    state = persistence.box_state(cfg.ssh_host_alias)
    return {"reachable": True, **state}


# --- the dashboard -----------------------------------------------------------


def _loading(text: str) -> Text:
    return Text.assemble((menu.spinner_frame() + " ", "kdev.accent"), (text, "kdev.muted"))


def _box_line(cfg: config.Config, live: menu.Live) -> RenderableType:
    if not cfg.notebook:
        return Text.assemble((ui.g("warn") + " ", "kdev.warn"), ("no workspace yet", "kdev.warn"))
    if not live.ready("box"):
        return _loading("checking the box…")
    box = live.get("box")
    if not isinstance(box, dict):
        return Text.assemble(
            (ui.g("warn") + " ", "kdev.warn"),
            ("could not read the box", "kdev.warn"),
            (f"  {live.errors.get('box', '')[:60]}", "kdev.muted"),
        )
    if box.get("saving"):
        return Text.assemble(
            (ui.g("live") + " ", "kdev.warn"),
            ("stopping", "bold"),
            ("  saving your files", "kdev.muted"),
        )
    if not box.get("running"):
        return Text.assemble(
            (ui.g("pending") + " ", "kdev.muted"),
            ("stopped", "bold"),
            (f"  {ui.g('bullet')}  next box {box_label(cfg)}", "kdev.muted"),
        )
    word = "starting" if box.get("queued") else "running"
    head = Text.assemble((ui.g("live") + " ", "kdev.ok"), (word, "kdev.running"))
    reach = live.get("reach") if live.ready("reach") else None
    if not live.ready("reach"):
        head.append("  ")
        head.append(_loading("reaching it over ssh…"))
        return head
    if not isinstance(reach, dict) or not reach.get("reachable"):
        head.append(f"  {ui.g('bullet')}  ", "kdev.muted")
        head.append("not reachable yet", "kdev.warn")
        return head
    if reach.get("run_by"):
        head.append(f"  {ui.g('bullet')}  started by {reach['run_by']}", "kdev.muted")
    head.append(f"  {ui.g('bullet')}  ", "kdev.muted")
    head.append(f"ssh {cfg.ssh_host_alias}", "kdev.cmd")
    ends, started = ui.epoch(reach.get("ends")), ui.epoch(reach.get("started"))
    if ends:
        return Group(head, ui.countdown(started or ends - 12 * 3600, ends, width=12))
    return head


def _quota_block(cfg: config.Config, live: menu.Live, width: int) -> RenderableType:
    if not live.ready("quota"):
        return _loading("reading quota…")
    rows = live.get("quota")
    if not isinstance(rows, list) or not rows:
        return Text("could not read quota", style="kdev.warn")
    next_hours = cfg.default_hours or 9.0
    gpu_box = (cfg.default_gpu or "t4") not in ("none", "tpu")
    total = sum(r.gpu_left for r in rows if not r.error)
    refill = quota.resets(rows)
    if len(rows) > 4 or width < 72:
        best = max((r for r in rows if not r.error), key=lambda r: r.gpu_left, default=None)
        line = Text.assemble((ui.fmt_hours(total), "bold"), (" of GPU left", "kdev.muted"))
        line.append(f"  {ui.g('bullet')}  {len(rows)} accounts", "kdev.muted")
        if refill:
            line.append(f"  {ui.g('bullet')}  refills {ui.fmt_when(refill)}", "kdev.muted")
        if best and line.cell_len + len(best.name) + 20 < width - 14:
            line.append(f"  {ui.g('bullet')}  most: {best.name} {best.hours}", "kdev.muted")
        return line
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    for r in rows:
        name = Text.assemble(
            (ui.g("live") if r.active else " ", "kdev.accent"), " ", (r.name, "bold")
        )
        if r.error:
            grid.add_row(name, Text(r.error[:40], style="kdev.err"), Text(""))
            continue
        ghost = 0.0
        after = ""
        box = live.get("box") if live.ready("box") else None
        box_running = isinstance(box, dict) and box.get("running")
        if r.active and gpu_box and r.gpu_total and width >= 92 and not box_running:
            ghost = min(next_hours * 3600, r.gpu_left) / r.gpu_total
            left_after = max(r.gpu_left - next_hours * 3600, 0)
            after = f"{ui.g('arrow')} {ui.fmt_hours(left_after)} after a {next_hours:g}h box"
        meter = Text.assemble(
            ui.bar(r.fraction, width=12, ghost=ghost), (f"  {r.hours:>6}", "bold")
        )
        grid.add_row(name, meter, Text(after, style="kdev.muted"))
    if refill:
        grid.add_row(Text(""), Text(f"refills {ui.fmt_when(refill)}", style="kdev.muted"), Text(""))
    return grid


def _rows() -> int:
    return shutil.get_terminal_size((80, 24)).lines


def _dashboard(cfg: config.Config, live: menu.Live, width: int) -> RenderableType:
    rows: list[tuple[str, RenderableType]] = [
        ("box", _box_line(cfg, live)),
        ("quota", _quota_block(cfg, live, width)),
    ]
    # Where it is reached is the least urgent fact: first to go when short.
    if width >= 60 and _rows() >= 24:
        rows.append(("workspace", Text(cfg.notebook or "—", style="kdev.muted")))
        rows.append(
            (
                "tunnel",
                Text(cfg.tunnel_hostname or "quick (new hostname each session)", "kdev.muted"),
            )
        )
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="kdev.muted", justify="right", no_wrap=True, min_width=9)
    grid.add_column(overflow="ellipsis", no_wrap=True)
    for label, value in rows:
        grid.add_row(label, value)
    return Padding(grid, (0, 0, 0, ui.GUTTER))


def _ansi(renderable: RenderableType, width: int) -> menu.Fragments:
    """Draw a rich renderable for prompt_toolkit, at the menu's width."""
    out = StringIO()
    target = Console(
        file=out,
        width=width,
        force_terminal=True,
        color_system="truecolor" if ui.console.color_system == "truecolor" else "256",
        no_color=ui.console.no_color or ui._no_colour(),
        theme=ui.THEME,
        legacy_windows=False,
    )
    target.print(renderable)
    return to_formatted_text(ANSI(out.getvalue().rstrip("\n")))


# --- what you can do ----------------------------------------------------------


def _items(cfg: config.Config, live: menu.Live) -> list[menu.Item]:
    box = live.get("box") if live.ready("box") else None
    known = isinstance(box, dict)
    running = known and bool(box.get("running")) and not box.get("saving")
    reach = live.get("reach") if live.ready("reach") else None
    reachable = isinstance(reach, dict) and bool(reach.get("reachable"))
    waiting = "" if known else "checking the box…"
    off = waiting or ("no box is running" if not running else "")

    items: list[menu.Item] = []
    if not cfg.notebook:
        items.append(menu.Item("1", "Join your group's workspace", ("workspace", "join")))
    elif running:
        editor = session.editor()
        if editor:
            items.append(
                menu.Item("1", "Open in VS Code", ("up", "--open", "code"), note=f"via {editor}")
            )
        items.append(
            menu.Item(
                str(len(items) + 1),
                "Open a shell",
                ("ssh",),
                note=f"ssh {cfg.ssh_host_alias}",
                disabled="" if reachable or not live.ready("reach") else "ssh does not answer yet",
            )
        )
        items.append(
            menu.Item(str(len(items) + 1), "Forward a port", ("forward",), note="localhost → box")
        )
        items.append(
            menu.Item(str(len(items) + 1), "Stop the box", ("down",), note="files are saved")
        )
    else:
        on = f"on {cfg.active}" if cfg.active else ""
        items.append(
            menu.Item("1", "Start the box", ("up",), note=f"{box_label(cfg)}  {on}".strip())
        )
        items.append(
            menu.Item(
                "2",
                "Start a CPU box",
                ("up", "--gpu", "none", "--hours", "2"),
                note="2h · no GPU quota",
            )
        )
        items.append(menu.Item("3", "Open a shell", ("ssh",), disabled=off))
        items.append(menu.Item("4", "Stop the box", ("down",), disabled=off))

    more = [
        menu.Item("s", "Status", ("status",), group="more"),
        menu.Item("l", "Logs", ("logs",), group="more"),
        menu.Item("h", "History", ("history",), group="more"),
        menu.Item("a", "Accounts", ("account",), group="more"),
    ]
    if len(cfg.profiles) > 1:
        more.append(menu.Item("u", "Switch account", ("account", "use"), group="more"))
    more += [
        menu.Item("w", "Workspace", ("workspace",), group="more"),
        menu.Item("c", "Settings", ("config",), group="more"),
        menu.Item("d", "Doctor", ("doctor",), group="more"),
        menu.Item("?", "All commands", ("--help",), group="more"),
    ]
    return items + more


#: Picks that hand you the box, or end with it gone: the menu's job is done.
_LEAVES = {
    ("up",),
    ("up", "--open", "code"),
    ("ssh",),
    ("up", "--gpu", "none", "--hours", "2"),
    # Kaggle still says "running" while the box saves: coming back to the
    # menu would show a box you just stopped as up.
    ("down",),
}


def _ask_args(item: menu.Item) -> list[str]:
    """The arguments an item needs that only you can give."""
    argv = list(item.argv)
    if argv == ["forward"]:
        port = ui.ask("Port on the box (or LOCAL:REMOTE)", default="8888")
        argv.append(port)
    return argv


def overview(run_command: Callable[[list[str]], None]) -> None:
    """The home screen. `run_command(argv)` dispatches a picked action."""
    cfg = config.load()
    if not cfg.profiles:
        if ui.interactive():
            wizard.run()
            return
        ui.err("kdev is not set up on this machine.", "Run `kdev setup` in a terminal.")
        raise typer.Exit(1)

    if not ui.interactive():
        _plain(cfg)
        return

    # The logo is a welcome, not information: it gives way to the menu in a
    # short terminal (an editor's panel is often 15 rows).
    ui.header(logo=_rows() >= LOGO_ROWS)
    while True:
        cfg = config.load()
        live = menu.Live()

        def load_box(cfg: config.Config = cfg, live: menu.Live = live) -> dict:
            box = _box_facts(cfg)
            if box.get("running") and not box.get("saving"):
                live.load("reach", lambda: _reach_facts(cfg))
            return box

        live.load("box", load_box)
        live.load("quota", lambda cfg=cfg: quota.rows(cfg))
        picker = menu.Menu(
            items=lambda cfg=cfg, live=live: _items(cfg, live),
            dashboard=lambda width, cfg=cfg, live=live: _ansi(_dashboard(cfg, live, width), width),
            live=live,
        )
        picked = picker.run()
        if picked is None:
            return
        try:
            argv = _ask_args(picked)
        except Cancelled:
            continue
        # The pick, as the command you could have typed: the menu's header for it.
        ui.header(" ".join(argv))
        leaves = tuple(picked.argv) in _LEAVES
        if leaves:
            _flush_input()
            with ui.header_given():
                run_command(argv)
            return
        try:
            with ui.header_given():
                run_command(argv)
        except typer.Exit as e:
            if e.exit_code not in (0, None):
                ui.hint(f"exited with status {e.exit_code}")
        except Cancelled:
            ui.hint("cancelled")
        except KdevError as e:
            ui.err(e.message, e.hint)
        except (api.KaggleError, auth.AuthError, tunnel.TunnelError) as e:
            ui.err(str(e))
        except KeyboardInterrupt:
            ui.blank()
        ui.blank()


def _flush_input() -> None:
    """Drop keys typed ahead while the menu closed: they belong to kdev, not
    to the remote shell that is about to open."""
    try:
        import sys
        import termios

        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (ImportError, OSError, ValueError):
        pass


def _plain(cfg: config.Config) -> None:
    """The overview without a terminal: plain, greppable lines."""
    ui.header(terminal_only=True)
    try:
        box = _box_facts(cfg)
    except (api.KaggleError, KdevError):
        box = {"status": "UNKNOWN", "running": False}
    rows = quota.rows(cfg)
    running = bool(box.get("running"))
    word = "running" if running else "stopping" if box.get("saving") else "stopped"
    ui.console.print(
        ui.facts(
            [
                ("box", word if cfg.notebook else "no workspace yet"),
                ("workspace", cfg.notebook or "—"),
                ("next box", box_label(cfg)),
                ("tunnel", cfg.tunnel_hostname or "quick"),
            ]
        )
    )
    ui.blank()
    ui.console.print(ui.accounts_table(rows))
    ui.blank()
    steps = (
        [("kdev ssh", "open a shell"), ("kdev down", "stop it")]
        if running
        else [("kdev up", "start the box"), ("kdev account", "accounts and quota")]
    )
    ui.next_steps(steps)
