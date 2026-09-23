"""`kdev config`: see and change this machine's settings, one at a time."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.text import Text

from .. import api, config, tunnel, ui
from ..errors import KdevError
from ._common import cap_for

app = typer.Typer(
    help="See and change settings.", no_args_is_help=False, invoke_without_command=True
)


def _gpu(value: str, _cfg: config.Config) -> str:
    if value not in api.SHAPES:
        raise KdevError(f"gpu must be one of: {', '.join(api.SHAPES)}.")
    return value


def _hours(value: str, cfg: config.Config) -> float:
    try:
        hours = float(value)
    except ValueError as e:
        raise KdevError(f"{value!r} is not a number of hours.") from e
    cap = cap_for(cfg.default_gpu)
    if not 0 < hours <= cap:
        raise KdevError(f"hours must be more than 0 and at most {cap:g}.")
    return hours


def _file(kind: str) -> Callable[[str, config.Config], str]:
    def read(value: str, _cfg: config.Config) -> str:
        path = Path(value).expanduser()
        if not path.is_file():
            raise KdevError(f"{value} is not a file.", f"Pass the path to the {kind}.")
        return path.read_text().strip()

    return read


def _public_key(value: str, cfg: config.Config) -> str:
    key = _file("public key (.pub)")(value, cfg)
    if not re.match(r"(ssh-|ecdsa-|sk-)", key):
        raise KdevError(
            f"{value} does not look like an SSH public key.",
            "It should be the .pub file, e.g. ~/.ssh/id_ed25519.pub",
        )
    return key


def _alias(value: str, _cfg: config.Config) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise KdevError("ssh-alias is a single word: letters, digits, dot, dash, underscore.")
    return value


def _hostname(value: str, _cfg: config.Config) -> str:
    try:
        return tunnel.validate_hostname(value)
    except tunnel.TunnelError as e:
        raise KdevError(str(e)) from e


def _text(value: str, _cfg: config.Config) -> str:
    return value.strip()


@dataclass(frozen=True)
class Setting:
    attr: str
    parse: Callable[[str, config.Config], object]
    help: str
    secret: bool = False


SETTINGS: dict[str, Setting] = {
    "gpu": Setting("default_gpu", _gpu, "accelerator for kdev up"),
    "hours": Setting("default_hours", _hours, "session length for kdev up"),
    "git-remote": Setting("git_remote", _text, "git repo synced with the box"),
    "git-deploy-key": Setting(
        "git_deploy_key", _file("private deploy key"), "its deploy key (a file path)", secret=True
    ),
    "ssh-key": Setting("ssh_public_key", _public_key, "this machine's key (.pub path)"),
    "ssh-alias": Setting("ssh_host_alias", _alias, "name in ~/.ssh/config"),
    "tunnel-hostname": Setting("tunnel_hostname", _hostname, "the box's hostname"),
    "tunnel-token": Setting("tunnel_token", _text, "dashboard tunnel token", secret=True),
}


def _shown(cfg: config.Config, key: str) -> Text:
    s = SETTINGS[key]
    value = getattr(cfg, s.attr)
    if value in ("", 0, 0.0, None):
        return Text("not set", style="kdev.muted")
    if s.secret:
        return Text("set (hidden)", style="kdev.muted")
    if key == "ssh-key":
        return Text(str(value).split()[-1] if " " in str(value) else "set")
    if key == "hours":
        return Text(f"{value:g}h")
    if key == "gpu":
        return Text(ui.gpu_label(value))
    return Text(str(value))


def _key(name: str) -> Setting:
    if name not in SETTINGS:
        raise KdevError(f"No setting called {name!r}.", "Settings: " + ", ".join(SETTINGS))
    return SETTINGS[name]


@app.callback()
def main(ctx: typer.Context) -> None:
    """Show every setting."""
    if ctx.invoked_subcommand:
        return
    cfg = config.load()
    t = ui.table(
        [("setting", "left"), ("value", "left"), ("what it is", "left")],
        [
            (Text(k, style="bold"), _shown(cfg, k), Text(s.help, style="kdev.muted"))
            for k, s in SETTINGS.items()
        ],
    )
    ui.console.print(t)
    ui.blank()
    ui.hint(f"stored in {config.CONFIG_PATH}")
    ui.next_steps(
        [
            ("kdev config set <setting> <value>", "change one"),
            ("kdev config unset <setting>", "back to the default"),
        ]
    )


@app.command("set")
def set_(
    name: str = typer.Argument(..., help="The setting, e.g. gpu."),
    value: str = typer.Argument(..., help="Its new value (a path, for keys)."),
) -> None:
    """Change one setting."""
    cfg = config.load()
    setting = _key(name)
    setattr(cfg, setting.attr, setting.parse(value, cfg))
    config.save(cfg)
    ui.ok(Text.assemble((name, "bold"), " is now ", _shown(cfg, name)))


@app.command("unset")
def unset(name: str = typer.Argument(..., help="The setting to reset.")) -> None:
    """Put one setting back to its default."""
    cfg = config.load()
    setting = _key(name)
    setattr(cfg, setting.attr, config.Config.__dataclass_fields__[setting.attr].default)
    config.save(cfg)
    ui.ok(Text.assemble((name, "bold"), " is back to ", _shown(cfg, name)))
