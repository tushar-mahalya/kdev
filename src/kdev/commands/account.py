"""Kaggle accounts on this machine, and the weekly quota each has left."""

from __future__ import annotations

import typer

from .. import auth, config, quota, ui, wizard
from ..errors import KdevError

app = typer.Typer(
    help="Kaggle accounts on this machine, and their quota.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@app.callback()
def main(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead."),
) -> None:
    """List the accounts with the GPU and TPU hours each has left this week."""
    if ctx.invoked_subcommand:
        return
    cfg = config.load()
    if not cfg.profiles:
        raise KdevError("No accounts yet.", "Sign one in: kdev account add")
    with ui.spinner("reading quota…"):
        rows = quota.rows(cfg)
    if as_json:
        ui.emit_json(
            [
                {
                    "account": r.name,
                    "username": r.username,
                    "active": r.active,
                    "gpu_seconds_left": r.gpu_left,
                    "gpu_seconds_total": r.gpu_total,
                    "tpu_seconds_left": r.tpu_left,
                    "error": r.error or None,
                }
                for r in rows
            ]
        )
        return
    ui.console.print(ui.accounts_table(rows))
    total = sum(r.gpu_left for r in rows if not r.error)
    ui.blank()
    ui.hint(
        f"{ui.fmt_hours(total)} of GPU left across {len(rows)} account(s)  "
        f"{ui.g('bullet')}  {ui.g('live')} runs the next box"
    )
    broken = [r.name for r in rows if r.error]
    if broken:
        ui.blank()
        ui.next_steps([(f"kdev account add {n}", "sign it in again") for n in broken])


@app.command("add")
def add(name: str = typer.Argument("", help="A short name for the account, e.g. alice.")) -> None:
    """Sign a Kaggle account in (opens a browser). Re-run to refresh one."""
    cfg = config.load()
    if not name and not ui.interactive():
        raise KdevError("Name the account.", "kdev account add <name>")
    if name:
        try:
            name = config.valid_profile_name(name)
        except ValueError as e:
            raise KdevError(str(e)) from e
    if name in cfg.profiles:
        _refresh(cfg, name)
        return
    name = wizard.sign_in(cfg, name)
    config.save(cfg)
    ui.ok(
        f"added {name} ({cfg.profiles[name].username})"
        + ("; it runs the next box" if cfg.active == name else "")
    )
    ui.blank()
    ui.next_steps([("kdev account", "see every account's quota")])


def _refresh(cfg: config.Config, name: str) -> None:
    """Sign an existing account in again, without risking its current token.

    The sign-in lands in a scratch slot first: if the browser was logged in as
    someone else, the account's working token must not have been replaced.
    """
    scratch = f"{name}.signing-in"
    ui.hint("Your browser will open to sign in to Kaggle.")
    try:
        username = auth.login(scratch)
    except auth.AuthError as e:
        auth.forget(scratch)
        raise KdevError(str(e), f"Try again: kdev account add {name}") from e
    expected = cfg.profiles[name].username
    if username != expected:
        auth.forget(scratch)
        raise KdevError(
            f"The browser signed in as {username}, not {expected}.",
            f"Switch Kaggle accounts in the browser, then: kdev account add {name}",
        )
    auth.creds_path(scratch).replace(auth.creds_path(name))
    ui.ok(f"signed {name} in again")


@app.command("use")
def use(name: str = typer.Argument("", help="The account to make active.")) -> None:
    """Choose which account runs the next box."""
    cfg = config.load()
    if not cfg.profiles:
        raise KdevError("No accounts yet.", "Sign one in: kdev account add")
    if not name:
        if not ui.interactive():
            raise KdevError("Name the account.", "kdev account use <name>")
        with ui.spinner("reading quota…"):
            rows = quota.rows(cfg)
        name = ui.pick_profile(rows, 0)
    cfg.profile(name)
    cfg.active = name
    config.save(cfg)
    ui.ok(f"{name} runs the next box")


@app.command("remove")
def remove(
    name: str = typer.Argument(..., help="The account to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Sign an account out and forget it on this machine."""
    cfg = config.load()
    prof = cfg.profile(name)
    if len(cfg.profiles) == 1:
        raise KdevError(
            "That is the only account; kdev needs one.", "Add another first: kdev account add"
        )
    if (
        not yes
        and ui.interactive()
        and not ui.confirm(f"Remove {name} ({prof.username}) from this machine?", default=False)
    ):
        return
    auth.forget(name)
    del cfg.profiles[name]
    if cfg.active == name:
        cfg.active = next(iter(cfg.profiles))
    config.save(cfg)
    ui.ok(f"removed {name}; {cfg.active} runs the next box")
