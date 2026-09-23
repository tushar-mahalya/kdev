"""The shared notebook: the one box every account in the group runs.

A Kaggle group cannot own anything -- "groups can never own resources", in
Kaggle's own words. So one account owns the notebook and shares it with the
group as Editor (Kaggle's "Can Edit"). Every member can then run it on their
own quota (measured: a session is charged to the account that starts it) and
read every version it has saved, which is what carries your files between
accounts.
"""

from __future__ import annotations

import re

from . import api, bootstrap, config, ui
from .errors import KdevError

PLACEHOLDER = "# kdev workspace notebook -- source is generated per session.\n"


def notebook_url(slug: str) -> str:
    return f"https://www.kaggle.com/code/{slug}/edit"


def slugify(title: str) -> str:
    """Kaggle's rule: the slug is the title, lowercased, dashes for the rest."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def valid_ref(ref: str) -> str:
    ref = (ref or "").strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[a-z0-9][a-z0-9-]*", ref):
        raise KdevError(
            f"{ref!r} is not a notebook reference.",
            "Use owner/notebook-slug, as in kaggle.com/code/owner/notebook-slug",
        )
    return ref


# --- reading ------------------------------------------------------------------


def discover(creds: api.Creds) -> list[tuple[str, str]]:
    """Notebooks shared with this account."""
    return [
        (k["ref"], k.get("title") or k["ref"])
        for k in api.list_shared_kernels(creds)
        if k.get("ref")
    ]


def scan(creds: api.Creds) -> tuple[list[dict], list[dict]]:
    """Which groups this account is in, and which notebooks each shares.

    Kaggle has no API that lists your groups (they are private and
    invitation-only), so they are derived from the notebooks shared with you
    and the sharing policy on each. A group with no shared notebook is
    therefore invisible, which is why creating the first box asks for a slug.
    """
    notebooks = []
    for ref, title in discover(creds):
        kid = api.kernel_id(creds, ref)
        groups = api.policy_groups(api.get_policy(creds, kid)) if kid else []
        notebooks.append({"ref": ref, "title": title, "id": kid, "groups": groups})
    by_slug: dict[str, dict] = {}
    for nb in notebooks:
        for g in nb["groups"]:
            by_slug.setdefault(g["slug"], {**g, "notebooks": []})["notebooks"].append(nb)
    return list(by_slug.values()), notebooks


def describe(cfg: config.Config, creds: api.Creds) -> dict:
    """Everything `kdev workspace` shows, as data."""
    out: dict = {
        "notebook": cfg.notebook,
        "group": cfg.group_slug,
        "role": "",
        "members": [],
        "version": 0,
        "files": None,
        "errors": [],
    }
    if not cfg.notebook:
        return out
    try:
        meta = api.get_kernel(creds, cfg.notebook)
        out["version"] = int(meta.get("currentVersionNumber") or 0)
        out["kernel_type"] = meta.get("kernelType", "")
        cfg.notebook_id = cfg.notebook_id or int(meta.get("id") or 0)
    except (api.KaggleError, TypeError, ValueError) as e:
        out["errors"].append(f"notebook: {e}")
    if cfg.group_slug and cfg.notebook_id:
        try:
            out["role"] = api.group_role(api.get_policy(creds, cfg.notebook_id), cfg.group_slug)
        except api.KaggleError as e:
            out["errors"].append(f"sharing: {e}")
        try:
            out["members"] = [
                m.get("username", "?") for m in api.group_members(creds, cfg.group_slug)
            ]
        except api.KaggleError as e:
            out["errors"].append(f"members: {e}")
    try:
        out["files"] = len(api.session_output(creds, cfg.notebook))
    except api.KaggleError:
        out["files"] = None
    return out


def adopt_config(cfg: config.Config, creds: api.Creds) -> list[str]:
    """Take the tunnel and git settings the notebook already runs with.

    A second laptop joining the workspace would otherwise redo the whole
    Cloudflare setup to reach a box the first laptop already knows how to
    reach. Only fills what is empty here, so a machine's own explicit settings
    always win. Returns the names of the settings it took.
    """
    if not cfg.notebook:
        return []
    try:
        found = bootstrap.extract_config(api.kernel_source(creds, cfg.notebook))
    except api.KaggleError:
        return []
    has_tunnel = bool(cfg.tunnel_credentials or cfg.tunnel_token)
    taken = []
    for key, value in found.items():
        if key.startswith("tunnel_") and has_tunnel:
            continue
        if not getattr(cfg, key):
            setattr(cfg, key, value)
            taken.append(key)
    return taken


# Kept under its earlier name for callers written against it.
adopt_notebook_config = adopt_config


# --- changing -----------------------------------------------------------------


def create(creds: api.Creds, owner: str, title: str) -> tuple[str, int]:
    """Create the workspace notebook. Never over an existing one.

    SaveKernel on a name that already exists replaces its source -- so a typo
    that matched a real notebook would wipe it. Check first.
    """
    slug = f"{owner}/{slugify(title)}"
    if not slugify(title):
        raise KdevError(f"{title!r} makes an empty notebook name.", "Use letters or digits.")
    try:
        existing = api.get_kernel(creds, slug)
    except api.KaggleError:
        existing = {}
    if existing.get("id"):
        raise KdevError(
            f"{slug} already exists.",
            f"Use it with: kdev workspace use {slug}   or pick another --name",
        )
    resp = api.save_kernel(
        creds,
        slug=slug,
        title=title,
        source=PLACEHOLDER,
        machine_shape=None,
        timeout_seconds=600,
        run=False,
    )
    kernel_id = int(resp.get("kernelId") or api.get_kernel(creds, slug).get("id") or 0)
    if not kernel_id:
        raise KdevError(
            f"Created {slug} but could not read its id back.",
            "Retry sharing with: kdev workspace share",
        )
    return slug, kernel_id


def share(creds: api.Creds, kernel_id: int, group_slug: str) -> None:
    try:
        api.share_with_group(creds, kernel_id, group_slug)
    except api.KaggleError as e:
        raise KdevError(
            f"Could not share with the group {group_slug!r}: {e}",
            "Check the slug (kaggle.com/groups/<slug>) and that the owner is a member.",
        ) from e


def use(cfg: config.Config, creds: api.Creds, ref: str) -> None:
    """Point this machine at an existing notebook."""
    ref = valid_ref(ref)
    kid = api.kernel_id(creds, ref)
    if not kid:
        raise KdevError(
            f"Could not open {ref}.",
            "Is it shared with this account? kdev workspace join lists what is.",
        )
    groups = api.policy_groups(api.get_policy(creds, kid))
    cfg.notebook, cfg.notebook_id = ref, kid
    cfg.group_slug = groups[0]["slug"] if groups else ""


def join(cfg: config.Config, creds: api.Creds, owner: str) -> bool:
    """Interactive: pick the group, then a notebook in it or a new one."""
    from . import safety

    with ui.spinner("reading your groups and notebooks…"):
        groups, _ = scan(creds)

    if not groups:
        ui.warn("No group shares a notebook with this account yet.")
        ui.hint("A group's slug is the end of its address: kaggle.com/groups/<slug>")
        slug = ui.ask("Group slug", default=cfg.group_slug)
        if not slug:
            return False
        return _create_and_share(cfg, creds, owner, slug)

    if len(groups) == 1:
        group = groups[0]
        ui.ok(f"group {group['name']} ({group['members']} members)")
    else:
        pick = ui.choose(
            "Which group?",
            [
                (
                    g["slug"],
                    f"{g['name']:<24} {g['members']} members · {len(g['notebooks'])} notebook(s)",
                )
                for g in groups
            ],
        )
        group = next(g for g in groups if g["slug"] == pick)

    options = []
    for nb in group["notebooks"]:
        try:
            verdict, _, _ = safety.check(creds, nb["ref"])
        except api.KaggleError:
            verdict = "occupied"
        tag = "kdev box" if verdict == "safe" else "has its own code (kept below kdev's cell)"
        options.append((nb["ref"], f"{nb['title']:<28} {tag}"))
    options.append(("", "+ New notebook for this group"))
    pick = ui.choose(f"Which notebook in {group['name']}?", options) if len(options) > 1 else ""
    if pick:
        cfg.notebook = pick
        cfg.notebook_id = next(nb["id"] for nb in group["notebooks"] if nb["ref"] == pick)
        cfg.group_slug = group["slug"]
        ui.ok(f"workspace {cfg.notebook}")
        return True
    return _create_and_share(cfg, creds, owner, group["slug"])


def _create_and_share(cfg: config.Config, creds: api.Creds, owner: str, group_slug: str) -> bool:
    title = ui.ask("Name for the new notebook", default="kdev-box")
    with ui.spinner("creating the notebook…"):
        slug, kid = create(creds, owner, title)
    ui.ok(f"created {slug}")
    with ui.spinner(f"sharing with {group_slug}…"):
        share(creds, kid, group_slug)
    ui.ok(f"shared with {group_slug} (Can Edit)")
    cfg.notebook, cfg.notebook_id, cfg.group_slug = slug, kid, group_slug
    return True
