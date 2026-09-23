"""Small pieces every command needs."""

from __future__ import annotations

from .. import config, ui, wizard
from ..errors import KdevError

MAX_HOURS = 12.0  # Kaggle's ceiling for CPU/GPU sessions
TPU_HOURS = 9.0


def cap_for(gpu: str) -> float:
    return TPU_HOURS if gpu == "tpu" else MAX_HOURS


def load_ready(interactive: bool) -> config.Config:
    """The config, running setup first if nothing is configured yet."""
    cfg = config.load()
    if cfg.profiles:
        return cfg
    if not interactive:
        raise KdevError("kdev is not set up on this machine.", "Run `kdev setup` in a terminal.")
    return wizard.run()


def need_notebook(cfg: config.Config, override: str = "") -> str:
    if not cfg.profiles:
        raise KdevError("kdev is not set up on this machine.", "Run: kdev setup")
    target = override or cfg.notebook
    if not target:
        raise KdevError(
            "No workspace notebook yet.",
            "Join your group's: kdev workspace join   or make one: "
            "kdev workspace create --group <slug>",
        )
    return target


def context(cfg: config.Config, account: str = "", notebook: str = "") -> list[tuple[str, str]]:
    """The facts a header shows: who, which notebook, which tunnel."""
    return [
        ("account", account or cfg.active or "—"),
        ("notebook", notebook or cfg.notebook or "—"),
        ("tunnel", cfg.tunnel_hostname or "quick"),
    ]


def box_label(cfg: config.Config) -> str:
    if not cfg.default_hours:
        return "not set"
    return f"{ui.gpu_label(cfg.default_gpu or 't4')} · {cfg.default_hours:g}h"
