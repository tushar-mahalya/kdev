"""Bare `kdev`: everything at a glance, then the next thing to do."""

from __future__ import annotations

import typer
from rich.text import Text

from .. import api, config, quota, session, ui, wizard
from ._common import box_label


def setup() -> None:
    """Get this machine ready. Safe to re-run: only does what is missing."""
    wizard.run()


def overview(run_command) -> None:
    """The home screen. `run_command(argv)` dispatches a picked action."""
    cfg = config.load()
    if not cfg.profiles:
        if ui.interactive():
            wizard.run()
            return
        ui.err("kdev is not set up on this machine.", "Run `kdev setup` in a terminal.")
        raise typer.Exit(1)

    ui.header()
    with ui.spinner("looking around…"):
        state = {}
        if cfg.notebook:
            try:
                state = api.session_status(cfg.profile().creds, cfg.notebook)
            except api.KaggleError:
                state = {}
        running = state.get("status") in api.LIVE_STATES
        reachable = running and session.reachable(cfg.ssh_host_alias)
        rows = quota.rows(cfg)

    box = Text.assemble(ui.live_dot(running), " ")
    if not cfg.notebook:
        box.append("no workspace yet", style="kdev.warn")
    elif running:
        box.append("running", style="bold kdev.ok")
        box.append("  reachable" if reachable else "  starting or unreachable", style="kdev.muted")
    else:
        box.append("stopped", style="bold")
        box.append(f"  files saved in {cfg.notebook}", style="kdev.muted")
    ui.console.print(
        ui.facts(
            [
                ("box", box),
                ("workspace", cfg.notebook or "—"),
                ("next box", box_label(cfg)),
                ("tunnel", cfg.tunnel_hostname or "quick"),
            ]
        )
    )
    ui.blank()
    ui.console.print(ui.accounts_table(rows))
    ui.blank()

    if not ui.interactive():
        steps = (
            [("kdev ssh", "open a shell"), ("kdev down", "stop it")]
            if running
            else [("kdev up", "start the box"), ("kdev account", "accounts and quota")]
        )
        ui.next_steps(steps)
        return

    actions: list[tuple[str, str]] = []
    if not cfg.notebook:
        actions.append(("workspace join", "Join your group's workspace"))
    elif running:
        actions += [
            ("up --open code", "Open it in VS Code"),
            ("ssh", "Open a shell"),
            ("status", "Details"),
            ("down", "Stop it"),
        ]
    else:
        actions += [
            ("up", f"Start the box      {box_label(cfg)}"),
            ("up --gpu none --hours 2", "Start a CPU box    uses no GPU quota"),
        ]
    if len(cfg.profiles) > 1:
        actions.append(("account use", "Switch account"))
    actions += [("account add", "Add an account"), ("doctor", "Check setup"), ("", "Quit")]
    picked = ui.choose("What now?", [(a, label) for a, label in actions])
    if picked:
        ui.blank()
        run_command(picked.split())
