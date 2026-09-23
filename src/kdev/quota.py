"""Weekly accelerator quota, per account, and choosing who runs a session.

Every account in the group carries its own allowance (measured: a session on
the shared notebook is charged to the account that starts it, not the owner),
so running short is a switch, not a wall.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import api, config, ui
from .errors import KdevError


def secs(value) -> int:
    """Durations arrive as protobuf Duration strings ('108000s') or numbers."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(str(value).rstrip("s") or 0))
    except ValueError:
        return 0


def remaining(quota: dict, kind: str = "gpuQuota") -> tuple[int, int]:
    """(seconds left, seconds allowed) for one quota block."""
    q = quota.get(kind) or {}
    total = secs(q.get("totalTimeAllowed"))
    used = secs(q.get("timeUsed")) + secs(q.get("timeReserved"))
    return max(total - used, 0), total


def _row(cfg: config.Config, name: str) -> ui.ProfileRow:
    prof = cfg.profiles[name]
    row = ui.ProfileRow(
        name=name, username=prof.username, gpu_left=0, gpu_total=0, active=name == cfg.active
    )
    try:
        q = api.quota(prof.creds)
    except api.KaggleError as e:
        row.error = "signed out: kdev account add " + name if e.status == 401 else str(e)[:48]
        return row
    row.gpu_left, row.gpu_total = remaining(q)
    row.tpu_left, row.tpu_total = remaining(q, "tpuQuota")
    return row


def rows(cfg: config.Config) -> list[ui.ProfileRow]:
    """Every account's quota, read in parallel -- one slow account must not
    make the whole screen wait N times over."""
    names = list(cfg.profiles)
    if not names:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
        return list(pool.map(lambda n: _row(cfg, n), names))


def pick_account(
    cfg: config.Config, explicit: str, hours: float, gpu: str, interactive: bool
) -> tuple[str, config.Profile]:
    """The active account runs it; kdev only steps in when it cannot finish.

    There is no reason to ask while the current account has room, so the
    question only appears when the answer matters.
    """
    if explicit:
        return explicit, cfg.profile(explicit)
    if gpu == "none" or len(cfg.profiles) == 1:
        # CPU sessions never touch GPU quota; one account has no alternative.
        return cfg.active, cfg.profile(cfg.active)

    with ui.spinner("checking quota…"):
        table = rows(cfg)
    need = hours * 3600
    active = next((r for r in table if r.name == cfg.active), None)
    if active and not active.error and active.gpu_left >= need:
        return cfg.active, cfg.profile(cfg.active)

    short = f"{active.hours} left" if active and not active.error else "quota unreadable"
    roomy = [r for r in table if not r.error and r.gpu_left >= need]
    ui.warn(f"{cfg.active} has {short}; this session needs {hours:g}h")

    if not interactive:
        if not roomy:
            raise KdevError(
                "No account has room for this session.",
                "Ask for fewer --hours, or --gpu none for a CPU box.",
            )
        best = max(roomy, key=lambda r: r.gpu_left)
        ui.hint(f"not a terminal, so using {best.name} ({best.hours} left)")
        return best.name, cfg.profile(best.name)

    if not roomy:
        ui.hint(f"No account has a full {hours:g}h left; Kaggle stops the box when it runs out.")
    name = ui.pick_profile(table, hours)
    if name != cfg.active and ui.confirm(f"Make {name} the active account?", default=True):
        cfg.active = name
        config.save(cfg)
    return name, cfg.profile(name)
