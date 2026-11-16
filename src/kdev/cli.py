"""kdev: a Kaggle notebook as your remote dev box.

Start it, connect from VS Code or a shell, and leave whenever you like; your
files come back on the next start, from any account in your group, on any
machine.
"""

from __future__ import annotations

import contextlib
import logging
import sys

import typer
import typer.main

from . import __version__, api, auth, tunnel, ui
from .commands import account, box, home, settings, workspace
from .commands import doctor as doctor_cmd
from .commands import tunnel as tunnel_cmd
from .errors import Cancelled, KdevError


def _brand_help() -> None:
    """--help in kdev's own palette: accent for what you type, grey for the rest."""
    import typer.rich_utils as ru

    ru.STYLE_OPTION = f"bold {ui.ACCENT}"
    ru.STYLE_SWITCH = ui.ACCENT
    ru.STYLE_NEGATIVE_OPTION = ui.ACCENT
    ru.STYLE_NEGATIVE_SWITCH = ui.ACCENT
    ru.STYLE_TYPES = ui.MUTED
    ru.STYLE_USAGE = f"bold {ui.ACCENT}"
    ru.STYLE_USAGE_COMMAND = "bold"
    ru.STYLE_COMMANDS_TABLE_FIRST_COLUMN = f"bold {ui.ACCENT}"
    ru.STYLE_OPTIONS_PANEL_BORDER = "grey42"
    ru.STYLE_COMMANDS_PANEL_BORDER = "grey42"
    ru.STYLE_HELPTEXT = ui.MUTED
    ru.STYLE_OPTION_DEFAULT = ui.MUTED


_brand_help()

EXAMPLES = """[bold]Examples[/bold]

  [bold #00b8d4]kdev[/]                          the home screen: the box, quota, one key to act
  [bold #00b8d4]kdev up[/]                       start the box, or connect to the one running
  [bold #00b8d4]kdev up --gpu none --hours 2[/]  a CPU box that uses no GPU quota
  [bold #00b8d4]kdev up --open code -y[/]        no questions; open VS Code when ready
  [bold #00b8d4]kdev ssh -- nvidia-smi[/]        run one command on the box
  [bold #00b8d4]kdev forward 8888[/]             reach the box's Jupyter on localhost:8888
  [bold #00b8d4]kdev status --json[/]            for scripts: JSON on stdout, nothing else
  [bold #00b8d4]kdev down[/]                     stop it; your files are saved either way

Docs: https://kdev-cli.pages.dev/docs/"""

app = typer.Typer(
    name="kdev",
    help=__doc__,
    epilog=EXAMPLES,
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
app.command("up", rich_help_panel="Box")(box.up)
app.command("down", rich_help_panel="Box")(box.down)
app.command(
    "ssh",
    rich_help_panel="Box",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)(box.ssh)
app.command("forward", rich_help_panel="Box")(box.forward)
app.command("status", rich_help_panel="Box")(box.status)
app.command("history", rich_help_panel="Files & history")(box.history)
app.command("logs", rich_help_panel="Box")(box.logs)
app.command("restore", rich_help_panel="Files & history")(box.restore_cmd)
app.command("backup", rich_help_panel="Files & history")(box.backup)
# Accounts, workspace, settings, tunnel ---------------------------------------
app.add_typer(account.app, name="account", rich_help_panel="Accounts & workspace")
app.add_typer(workspace.app, name="workspace", rich_help_panel="Accounts & workspace")
app.add_typer(settings.app, name="config", rich_help_panel="Setup")
app.add_typer(tunnel_cmd.app, name="tunnel", rich_help_panel="Setup")
app.command("doctor", rich_help_panel="Setup")(doctor_cmd.doctor)
app.command("setup", rich_help_panel="Setup")(home.setup)
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


def _quote_commands(message: str) -> str:
    """click quotes names as 'status'; kdev draws commands as commands."""
    import re

    return re.sub(r"'([^'\s]+)'", r"`\1`", message)


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
        with ui.command_header_scope():
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
        if len(sys.argv) > 1 and sys.argv[1] == "up":
            ui.errconsole.print(
                ui.Text(
                    "  A box may still be starting and using quota: "
                    "kdev up connects to it, kdev down stops it.",
                    style="kdev.muted",
                )
            )
        sys.exit(130)
    except BrokenPipeError:
        # `kdev logs | head -1`: the reader left, which is not a failure.
        import os

        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    except typer.Abort:
        ui.errconsole.print(ui.Text("\n  interrupted", style="kdev.muted"))
        sys.exit(130)
    except typer.TyperException as e:
        # Usage mistakes -- a bad option, a missing argument -- in the
        # parser's own words, which are good (including "Did you mean"), in
        # kdev's voice, with the help for the command that was being typed.
        ctx = getattr(e, "ctx", None)
        where = ctx.command_path if ctx is not None else "kdev"
        message = e.format_message() if hasattr(e, "format_message") else str(e)
        ui.err(_quote_commands(message), f"See `{where} --help`")
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
