"""kdev: a Kaggle notebook as your remote dev box.

Start it, connect from VS Code or a shell, and leave whenever you like; your
files come back on the next start, from any account in your group, on any
machine.
"""

from __future__ import annotations

import logging
import sys

import typer
import typer.main

from . import __version__, api, auth, tunnel, ui
from .commands import account, box, home, settings, workspace
from .commands import doctor as doctor_cmd
from .commands import tunnel as tunnel_cmd
from .errors import Cancelled, KdevError

app = typer.Typer(
    name="kdev",
    help=__doc__,
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    # Typer's default crash report prints every local variable -- which here
    # includes OAuth tokens and tunnel credentials. Never.
    pretty_exceptions_show_locals=False,
)


def _version(value: bool) -> None:
    if value:
        print(f"kdev {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version,
        is_eager=True,
        help="Print the version and exit.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Log every Kaggle API call to stderr."
    ),
) -> None:
    """With no command: the overview, and what to do next."""
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG, stream=sys.stderr, format="  %(name)s  %(message)s"
        )
        # Our own calls only; httpx's per-request chatter is noise here.
        for noisy in ("httpx", "httpcore", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    if ctx.invoked_subcommand is None:
        home.overview(_dispatch)


# The box -------------------------------------------------------------------
app.command("up")(box.up)
app.command("down")(box.down)
app.command("ssh", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(
    box.ssh
)
app.command("status")(box.status)
app.command("history")(box.history)
app.command("logs")(box.logs)
app.command("restore")(box.restore_cmd)
app.command("backup")(box.backup)
# Accounts, workspace, settings, tunnel ---------------------------------------
app.add_typer(account.app, name="account")
app.add_typer(workspace.app, name="workspace")
app.add_typer(settings.app, name="config")
app.add_typer(tunnel_cmd.app, name="tunnel")
app.command("doctor")(doctor_cmd.doctor)
app.command("setup")(home.setup)
# Muscle memory, not advertised ------------------------------------------------
app.command("ps", hidden=True)(box.status)
app.command("login", hidden=True)(account.add)


def _dispatch(argv: list[str]) -> None:
    """Run a command picked from the overview, as if it had been typed --
    including its exit status, which a non-standalone run returns rather than
    raises. Re-raised so it unwinds through `main` like a typed command's."""
    code = typer.main.get_command(app).main(args=argv, prog_name="kdev", standalone_mode=False)
    if isinstance(code, int) and code:
        raise typer.Exit(code)


def _explain(e: api.KaggleError) -> tuple[str, str]:
    text = str(e)
    if e.status == 401:
        return text, "Sign that account in again: kdev account add <name>"
    if e.status == 403:
        return text, "That account is not allowed to do this. Is it in the group? kdev workspace"
    if e.status == 404:
        return text, "Kaggle cannot find it. Check the workspace: kdev workspace"
    if e.status == 429:
        return text, "Kaggle is rate-limiting; wait a minute and retry."
    return text, "Run it again with -v to see each API call."


def main() -> None:
    verbose = any(a in ("-v", "--verbose") for a in sys.argv[1:3])
    try:
        # Not standalone, so errors reach the handlers below -- which means a
        # command's `typer.Exit(n)` comes back as a return value, not an exit.
        # Pass it on, or every non-zero status (doctor, ssh, restore) is lost.
        code = app(prog_name="kdev", standalone_mode=False)
        if isinstance(code, int) and code:
            sys.exit(code)
    except Cancelled:
        ui.errconsole.print(ui.Text("  cancelled", style="kdev.muted"))
        sys.exit(130)
    except KdevError as e:
        ui.err(e.message, e.hint)
        sys.exit(1)
    except api.KaggleError as e:
        ui.err(*_explain(e))
        sys.exit(1)
    except (auth.AuthError, tunnel.TunnelError) as e:
        ui.err(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        ui.errconsole.print(ui.Text("\n  interrupted", style="kdev.muted"))
        sys.exit(130)
    except typer.Abort:
        ui.errconsole.print(ui.Text("\n  interrupted", style="kdev.muted"))
        sys.exit(130)
    except typer.TyperException as e:
        # Usage mistakes -- a bad option, a missing argument -- in the
        # parser's own words, which are good, plus where to look.
        show = getattr(e, "show", None)
        if callable(show):
            show()
        else:
            ui.err(str(e), "kdev --help lists every command and option.")
        sys.exit(getattr(e, "exit_code", 2))
    except typer.Exit as e:
        sys.exit(e.exit_code)
    except SystemExit:
        raise
    except Exception as e:  # the last line of defence: never a raw traceback
        if verbose:
            raise
        ui.err(
            f"kdev hit an unexpected error: {type(e).__name__}: {e}",
            "Run it again with -v for the full traceback, and report it.",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
