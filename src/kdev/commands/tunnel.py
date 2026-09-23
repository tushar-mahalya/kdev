"""`kdev tunnel`: the Cloudflare tunnel that makes the box reachable."""

from __future__ import annotations

import typer

from .. import config, ui, wizard
from .. import tunnel as cf

app = typer.Typer(
    help="The Cloudflare tunnel the box is reached through.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@app.callback()
def main(ctx: typer.Context) -> None:
    """How the box is reached, and whether its DNS points the right way."""
    if ctx.invoked_subcommand:
        return
    cfg = config.load()
    from rich.text import Text

    if cfg.tunnel_credentials:
        rows: list[tuple[str, object]] = [
            ("kind", "named, managed by kdev"),
            ("name", cfg.tunnel_name),
            ("host", Text(cfg.tunnel_hostname, style="bold")),
        ]
        fix = ""
        if not cf.logged_in():
            rows.append(
                (
                    "dns",
                    Text("not checked: no Cloudflare login on this machine", style="kdev.muted"),
                )
            )
        else:
            with ui.spinner("checking DNS…"):
                rec = cf.dns_record(cfg.tunnel_hostname)
            want = cf.tunnel_target(cfg.tunnel_id)
            if rec and rec.get("content") == want:
                rows.append(("dns", Text(f"{ui.g('ok')} points at this tunnel", style="kdev.ok")))
            else:
                where = f"points at {rec.get('content')}" if rec else "has no record"
                rows.append(("dns", Text(f"{ui.g('err')} {where}", style="kdev.err")))
                fix = "kdev tunnel setup --force"
        ui.console.print(ui.facts(rows))
        if fix:
            ui.blank()
            ui.next_steps([(fix, "point the hostname back at the tunnel")])
    elif cfg.tunnel_token:
        ui.console.print(
            ui.facts(
                [
                    ("kind", "managed in the Cloudflare dashboard"),
                    ("host", cfg.tunnel_hostname or Text("not set", style="kdev.warn")),
                ]
            )
        )
    else:
        ui.console.print(
            ui.facts([("kind", Text("quick: a new hostname every session", style="kdev.warn"))])
        )
        ui.blank()
        ui.next_steps([("kdev tunnel setup", "one hostname that survives drops")])


@app.command("setup")
def setup(
    hostname: str = typer.Option("", "--hostname", help="Full hostname, instead of <name>.<zone>."),
    force: bool = typer.Option(False, "--force", help="Rebuild even if it already works."),
) -> None:
    """Create the named tunnel and its DNS record (opens a browser once)."""
    cfg = config.load()
    if wizard.setup_named_tunnel(cfg, hostname=hostname, force=force):
        config.save(cfg)
