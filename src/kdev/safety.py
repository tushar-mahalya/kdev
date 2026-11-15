"""Guard against overwriting a notebook someone is actually using.

`SaveKernel` replaces a notebook's entire source. kdev generates its own
bootstrap script, so pointing it at a notebook that holds real work destroys
that work -- and an uncommitted editor draft has no version history to restore
from. Nothing may be overwritten unless kdev wrote it, or the user says so
having been told exactly what is at stake.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import api, bootstrap
from . import config as _config


def backups_dir() -> Path:
    return _config.CONFIG_DIR / "backups"


def is_kdev_notebook(source: str) -> bool:
    return bootstrap.MARKER in source


def is_empty(source: str) -> bool:
    """Blank, or the placeholder `kdev workspace create` writes."""
    stripped = source.strip()
    return not stripped or "source is generated per session" in stripped


#: Every `kdev up` replaces the source, so every `kdev up` writes a backup.
#: Without a cap that directory grows for as long as the tool is used.
KEEP_BACKUPS = 20


def back_up(slug: str, source: str) -> Path | None:
    """Keep a local copy of whatever we are about to replace."""
    if not source.strip():
        return None
    prefix = slug.replace("/", "_")
    path = backups_dir() / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    prune(prefix)
    return path


def prune(prefix: str, keep: int = KEEP_BACKUPS) -> None:
    """Keep the newest `keep` backups for one notebook, drop the rest."""
    old = sorted(backups_dir().glob(f"{prefix}-*.txt"), key=lambda p: p.name, reverse=True)[keep:]
    for path in old:
        path.unlink(missing_ok=True)


def missing(e: api.KaggleError, creds: api.Creds, slug: str) -> bool:
    """Whether a failed GetKernel means the notebook is not there.

    Kaggle answers 403 "Permission 'kernels.get' was denied", not 404, for a
    slug that does not exist in your own namespace (measured). In anyone
    else's, a 403 may just mean "not shared with you", so only 404 counts.
    """
    if e.status == 404:
        return True
    owner = slug.partition("/")[0]
    return e.status == 403 and bool(owner) and owner.lower() == creds.username.lower()


def check(creds: api.Creds, slug: str) -> tuple[str, str, Path | None]:
    """Classify a notebook before kdev writes to it.

    Returns (verdict, source, backup_path) where verdict is "safe" when kdev
    owns the notebook or it is empty, and "occupied" when it holds other code.
    """
    try:
        source = api.kernel_source(creds, slug)
    except api.KaggleError as e:
        if not missing(e, creds, slug):
            # Offline, signed out, Kaggle down: the notebook may well exist
            # and hold work. "Could not look" is never "nothing there".
            raise
        # A notebook that does not exist yet is safe to create.
        return "safe", "", None

    backup = back_up(slug, source)
    if is_empty(source) or is_kdev_notebook(source):
        return "safe", source, backup
    return "occupied", source, backup
