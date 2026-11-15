import json

import pytest

from kdev import cloudflared, config, quota, ui
from kdev.errors import KdevError


def _accounts(monkeypatch, rows):
    cfg = config.Config()
    for r in rows:
        cfg.add(r.name, config.Profile(r.username))
    config.save(cfg)
    monkeypatch.setattr(quota, "rows", lambda cfg: rows)


def _kdev(monkeypatch, *argv) -> int:
    from kdev import cli

    monkeypatch.setattr("sys.argv", ["kdev", *argv])
    try:
        cli.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else int(e.code is not None)
    return 0


def test_account_json_has_every_total(monkeypatch, capsys):
    _accounts(
        monkeypatch,
        [ui.ProfileRow("alice", "alice", 3600, 108000, tpu_left=600, tpu_total=72000)],
    )
    assert _kdev(monkeypatch, "account", "--json") == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["tpu_seconds_total"] == 72000 and row["tpu_seconds_left"] == 600
    assert row["gpu_seconds_total"] == 108000


def test_offline_accounts_are_not_told_to_sign_in_again(monkeypatch, capsys):
    _accounts(
        monkeypatch,
        [
            ui.ProfileRow("alice", "alice", 0, 0, error=quota.OFFLINE),
            ui.ProfileRow("bob", "bob", 0, 0, error="signed out: kdev account add bob"),
        ],
    )
    assert _kdev(monkeypatch, "account") == 0
    out = capsys.readouterr().out
    assert "kdev account add bob" in out and "kdev account add alice" not in out
    assert "unexpected" not in out


def test_missing_cloudflared_points_at_a_command_that_exists(monkeypatch):
    monkeypatch.setattr(cloudflared, "find", lambda: None)
    with pytest.raises(KdevError, match="kdev doctor --fix"):
        cloudflared.ensure(auto=False)
