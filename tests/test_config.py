import json
import os

import pytest

from kdev import config


def test_config_roundtrip_is_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")

    from kdev.errors import KdevError

    cfg = config.Config(tunnel_credentials='{"TunnelSecret": "s"}')
    cfg.add("a", config.Profile("alice"))
    config.save(cfg)

    assert oct(os.stat(config.CONFIG_PATH).st_mode)[-3:] == "600", "tunnel secrets live in here"
    back = config.load()
    assert back.profile().username == "alice"
    assert back.tunnel_credentials == '{"TunnelSecret": "s"}'
    with pytest.raises(KdevError):
        back.profile("nope")


def test_a_config_from_an_older_kdev_still_loads(tmp_path, monkeypatch):
    """Legacy API keys and per-account notebook slugs are dropped, not fatal."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "profiles": {"a": {"username": "alice", "key": "old", "slug": "alice/nb"}},
                "active": "gone",
                "some_future_field": 1,
            }
        )
    )
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    cfg = config.load()
    assert cfg.profiles["a"].username == "alice"
    assert cfg.active == "a", "an active account that no longer exists falls back"


def test_a_crash_mid_save_never_leaves_a_truncated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.Config()
    cfg.add("a", config.Profile("alice"))
    config.save(cfg)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(config.json, "dump", boom)
    with pytest.raises(OSError):
        config.save(cfg)
    assert config.load().profiles["a"].username == "alice"


def test_loaded_profiles_know_their_own_name(tmp_path, monkeypatch):
    """Profile.creds finds the token file by `name`. Every caller but
    Config.profile() iterates cfg.profiles directly, so a name stamped only in
    Config.profile() meant the dashboard, the picker and `kdev up` all
    authenticated as nobody and got a 401."""
    from kdev import config as c

    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(c, "CONFIG_PATH", tmp_path / "config.json")
    cfg = c.Config()
    cfg.add("alice", c.Profile(username="a"))
    cfg.add("bob", c.Profile(username="b"))
    c.save(cfg)

    back = c.load()
    assert [p.name for p in back.profiles.values()] == ["alice", "bob"]
    assert back.active == "alice"


def test_unreadable_config_explains_itself(tmp_path, monkeypatch):
    from kdev import config as c

    path = tmp_path / "config.json"
    path.write_text("{not json")
    monkeypatch.setattr(c, "CONFIG_PATH", path)
    from kdev.errors import KdevError

    with pytest.raises(KdevError) as e:
        c.load()
    assert "kdev setup" in e.value.hint


@pytest.mark.parametrize(
    "bad", ["", "  ", "a" * 33, "../escape", "has space", "-leading", "with/slash", "╭──── panel"]
)
def test_profile_names_that_would_become_bad_filenames_are_rejected(bad):
    """A real config on this machine ended up with a Rich panel border as a
    profile name, because the prompt validated nothing and the name becomes a
    path under ~/.config/kdev/creds/."""
    from kdev import config as c

    with pytest.raises(ValueError):
        c.valid_profile_name(bad)


def test_profile_names_that_are_fine_survive_unchanged():
    from kdev import config as c

    for good in ("me", "alice", "acct-2", "a.b_c", "A1"):
        assert c.valid_profile_name(f"  {good} ") == good


def test_version_import_survives_a_source_checkout(monkeypatch):
    """`python -m kdev` from a clone must not die on an import-time lookup."""
    import importlib
    from importlib.metadata import PackageNotFoundError

    import kdev

    def missing(_name):
        raise PackageNotFoundError(_name)

    monkeypatch.setattr("importlib.metadata.version", missing)
    reloaded = importlib.reload(kdev)
    assert reloaded.__version__.endswith("+source")
    monkeypatch.undo()
    importlib.reload(kdev)


def test_saving_an_empty_config_over_a_real_one_is_refused(tmp_path, monkeypatch):
    """A half-built Config from a script would otherwise wipe every account,
    the notebook and the tunnel in one write."""
    from kdev import config as c

    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(c, "CONFIG_PATH", tmp_path / "config.json")
    real = c.Config(notebook="me/box", tunnel_hostname="box.example.com")
    real.add("alice", c.Profile(username="a"))
    c.save(real)

    with pytest.raises(ValueError, match="refusing to overwrite"):
        c.save(c.Config())
    assert c.load().notebook == "me/box"

    # A genuinely fresh machine still writes fine.
    (tmp_path / "config.json").unlink()
    c.save(c.Config())
    assert c.load().profiles == {}
