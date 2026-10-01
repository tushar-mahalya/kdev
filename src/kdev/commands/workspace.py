"""The shared workspace: one notebook, shared with your Kaggle group."""

from __future__ import annotations

import re

import typer
from rich.text import Text

from .. import api, config, kdev_box, persistence, ui
from .. import notebook as nb
from ..errors import KdevError
from ._common import need_notebook

app = typer.Typer(
    help="The shared notebook every account in your group runs.",
    no_args_is_help=False,
    invoke_without_command=True,
)


def _adopt(cfg: config.Config, creds: api.Creds) -> None:
    taken = nb.adopt_config(cfg, creds)
    if any(k.startswith("tunnel_") for k in taken):
        ui.ok(f"tunnel {cfg.tunnel_hostname or '(token)'}, taken from the notebook")
    if "git_remote" in taken:
        ui.ok(f"git remote {cfg.git_remote}, taken from the notebook")


@app.callback()
def main(
    ctx: typer.Context,
    account: str = typer.Option("", "--account", "-a", help="Look as this account."),
) -> None:
    """The notebook, its group and sharing, members and saved versions."""
    if ctx.invoked_subcommand:
        return
    cfg = config.load()
    ui.header("workspace", terminal_only=True)
    target = need_notebook(cfg)
    with ui.spinner("reading the workspace…"):
        info = nb.describe(cfg, cfg.profile(account).creds)
    role = info["role"]
    sharing: object
    if not cfg.group_slug:
        sharing = Text("not shared with a group", style="kdev.warn")
    elif role == api.ROLE_EDITOR:
        sharing = Text(f"{cfg.group_slug} can edit", style="kdev.ok")
    elif role:
        sharing = Text(
            f"{cfg.group_slug} is {role.rsplit('_', 1)[-1].lower()} only; members cannot run it",
            style="kdev.warn",
        )
    else:
        sharing = Text(f"{cfg.group_slug} is not on the notebook", style="kdev.err")
    files = info["files"]
    rows: list[tuple[str, object]] = [
        ("notebook", Text(target, style="bold")),
        ("owner", target.split("/")[0]),
        ("sharing", sharing),
    ]
    if info["members"]:
        rows.append(("members", ", ".join(info["members"])))
    rows.append(("saved", f"{info['saved']}, {files} file(s)" if info["saved"] else "nothing yet"))
    rows.append(("url", Text(nb.notebook_url(target), style="kdev.muted")))
    ui.card(Text("workspace", style="bold"), rows)
    for e in info["errors"]:
        ui.warn(e)
    fixes = []
    if cfg.group_slug and role != api.ROLE_EDITOR:
        fixes.append(("kdev workspace share", "give the group Can Edit again"))
    fixes.append(("kdev workspace files", "what the latest saved version holds"))
    ui.next_steps(fixes)


@app.command("files")
def files(
    version: str = typer.Option(
        "", "--version", help="A saved version, e.g. v12 (default: the newest with files)."
    ),
    account: str = typer.Option("", "--account", "-a", help="Read as this account."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead."),
) -> None:
    """What a saved version holds -- what `kdev up` restores."""
    cfg = config.load()
    if not as_json:
        ui.header("workspace files", terminal_only=True)
    target = need_notebook(cfg)
    if version and not re.fullmatch(r"v\d+", version):
        raise KdevError("--version takes a label like v12.")
    creds = cfg.profile(account).creds
    with ui.spinner("reading the saved version…"):
        try:
            if version:
                listing = [f["name"] for f in api.session_output(creds, target, version)]
            else:
                # The newest version with files: a running box's own has none.
                latest = int(api.get_kernel(creds, target).get("currentVersionNumber") or 0)
                version, found = persistence.newest_saved(creds, target, latest)
                listing = list(found)
        except api.KaggleError as e:
            raise KdevError(
                f"Could not read {version or 'the saved files'} of {target}.", str(e)
            ) from e
    # Exactly what a restore would bring back: not kdev's own state, not the
    # box's runtime files, not a download a killed restore left half-done.
    names = sorted(
        name
        for name in listing
        if not name.startswith(".kdev/")
        and name not in kdev_box.RUNTIME
        and not name.endswith(kdev_box.PART)
    )
    if as_json:
        ui.emit_json({"notebook": target, "version": version or None, "files": names})
        return
    ui.console.print(
        Text.assemble((target, "bold"), ("  " + (version or "no saved version"), "kdev.muted"))
    )
    for name in names[:50]:
        ui.hint(name)
    if len(names) > 50:
        ui.hint(f"… and {len(names) - 50} more (--json lists them all)")
    if not names:
        ui.hint("nothing saved yet")


@app.command("join")
def join(account: str = typer.Option("", "--account", "-a", help="Look as this account.")) -> None:
    """Pick your group's notebook from the ones shared with you."""
    if not ui.interactive():
        raise KdevError(
            "join asks which notebook to use.",
            "Without a terminal, name it: kdev workspace use owner/slug",
        )
    cfg = config.load()
    ui.header("workspace join", terminal_only=True)
    prof = cfg.profile(account)
    if nb.join(cfg, prof.creds, prof.username):
        _adopt(cfg, prof.creds)
        config.save(cfg)
        ui.blank()
        ui.next_steps([("kdev up", "start the box")])


@app.command("use")
def use(
    ref: str = typer.Argument(..., help="owner/notebook-slug"),
    account: str = typer.Option("", "--account", "-a", help="Look as this account."),
) -> None:
    """Point this machine at a notebook by name."""
    cfg = config.load()
    ui.header("workspace use", terminal_only=True)
    creds = cfg.profile(account).creds
    with ui.spinner("opening the notebook…"):
        nb.use(cfg, creds, ref)
    ui.ok(
        f"workspace {cfg.notebook}"
        + (f", shared with {cfg.group_slug}" if cfg.group_slug else ", not shared with a group")
    )
    _adopt(cfg, creds)
    config.save(cfg)


@app.command("create")
def create(
    group: str = typer.Option("", "--group", "-g", help="Your Kaggle group's slug."),
    name: str = typer.Option("kdev-box", "--name", help="Title of the new notebook."),
    account: str = typer.Option("", "--account", "-a", help="The account that will own it."),
) -> None:
    """Make a new notebook and share it with your group."""
    cfg = config.load()
    ui.header("workspace create", terminal_only=True)
    if not group:
        if not ui.interactive():
            raise KdevError("Name the group.", "kdev workspace create --group <slug>")
        ui.hint("A group's slug is the end of its address: kaggle.com/groups/<slug>")
        group = ui.ask("Group slug")
    prof = cfg.profile(account)
    with ui.spinner("creating the notebook…"):
        slug, kid = nb.create(prof.creds, prof.username, name)
    ui.ok(f"created {slug}")
    with ui.spinner(f"sharing with {group}…"):
        nb.share(prof.creds, kid, group)
    ui.ok(f"shared with {group} (Can Edit)")
    cfg.notebook, cfg.notebook_id, cfg.group_slug = slug, kid, group
    config.save(cfg)
    ui.blank()
    ui.next_steps(
        [
            ("kdev up", "start the box"),
            ("kdev workspace join", "what everyone else in the group runs"),
        ]
    )


@app.command("share")
def share(
    group: str = typer.Option("", "--group", "-g", help="Group slug (default: the workspace's)."),
    account: str = typer.Option("", "--account", "-a", help="The account that owns it."),
) -> None:
    """Give the group Can Edit on the notebook again."""
    cfg = config.load()
    ui.header("workspace share", terminal_only=True)
    target = need_notebook(cfg)
    slug = group or cfg.group_slug
    if not slug:
        raise KdevError("Which group?", "kdev workspace share --group <slug>")
    creds = cfg.profile(account).creds
    kid = cfg.notebook_id or api.kernel_id(creds, target)
    if not kid:
        raise KdevError(f"Could not open {target}.")
    with ui.spinner(f"sharing with {slug}…"):
        nb.share(creds, kid, slug)
    cfg.group_slug, cfg.notebook_id = slug, kid
    config.save(cfg)
    ui.ok(f"{target} is shared with {slug} (Can Edit)")
