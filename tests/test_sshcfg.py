import pytest

from kdev import sshcfg


def test_ssh_block_replaces_itself_and_preserves_neighbours(tmp_path, monkeypatch):
    cfgfile = tmp_path / "config"
    cfgfile.write_text("Host github.com\n    User git\n\nHost other\n    User me\n")
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfgfile)

    sshcfg.write("kaggle", "a.example.com")
    sshcfg.write("kaggle", "b.example.com")
    text = cfgfile.read_text()

    assert text.count(sshcfg.BEGIN) == 1, "rewrites must not stack up blocks"
    assert "b.example.com" in text and "a.example.com" not in text
    # The old line-scanning rewriter dropped whatever stanza came last.
    assert "Host github.com" in text and "Host other" in text and "User me" in text

    sshcfg.clear()
    assert sshcfg.BEGIN not in cfgfile.read_text()
    assert "Host other" in cfgfile.read_text()


def test_ssh_block_embeds_the_absolute_binary_path(tmp_path, monkeypatch):
    """ssh resolves a bare ProxyCommand from PATH, where the managed copy is not."""
    cfgfile = tmp_path / "config"
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfgfile)

    managed = tmp_path / "kdev bin" / "cloudflared"
    sshcfg.write("kaggle", "host.example.com", cloudflared_path=managed)
    text = cfgfile.read_text()
    assert f'ProxyCommand "{managed}" access ssh' in text


def test_ssh_block_presence_is_detectable(tmp_path, monkeypatch):
    """`kdev ssh` should say 'no session' rather than hand ssh a dead alias."""
    from kdev import sshcfg

    cfg = tmp_path / "config"
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfg)
    assert not sshcfg.has_block()
    sshcfg.write("kaggle", "box.example.com")
    assert sshcfg.has_block()
    sshcfg.clear()
    assert not sshcfg.has_block()


# --- placement --------------------------------------------------------------------

USER_CONFIG = """\
# my global settings
AddKeysToAgent yes
Include ~/.ssh/config.d/*

# work machines
Host github.com
    User git

Host *
    User nobody
    ServerAliveInterval 5
"""


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def test_the_block_goes_above_every_stanza_so_host_star_cannot_override_it(tmp_path, monkeypatch):
    """ssh uses the first value it finds: below `Host *`, our `User root` lost."""
    cfgfile = tmp_path / "config"
    cfgfile.write_text(USER_CONFIG)
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfgfile)
    sshcfg.write("kaggle", "box.example.com")
    text = cfgfile.read_text()

    assert text.index(sshcfg.BEGIN) < text.index("Host *")
    assert text.index(sshcfg.BEGIN) < text.index("Host github.com")
    # Global options apply to every host and stay on top; the comment above
    # a stanza stays with it.
    assert text.index("Include ~/.ssh/config.d/*") < text.index(sshcfg.BEGIN)
    assert text.index(sshcfg.END) < text.index("# work machines")
    # Nothing of the user's is lost or reordered.
    block = _lines(sshcfg.render_block("kaggle", "box.example.com"))
    assert [x for x in _lines(text) if x not in block] == _lines(USER_CONFIG)


def test_rewriting_is_idempotent(tmp_path, monkeypatch):
    cfgfile = tmp_path / "config"
    cfgfile.write_text(USER_CONFIG)
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfgfile)
    sshcfg.write("kaggle", "box.example.com")
    once = cfgfile.read_text()
    sshcfg.write("kaggle", "box.example.com")
    assert cfgfile.read_text() == once
    sshcfg.clear()
    assert _lines(cfgfile.read_text()) == _lines(USER_CONFIG)


def test_a_block_left_below_host_star_by_an_older_kdev_moves_up(tmp_path, monkeypatch):
    cfgfile = tmp_path / "config"
    old = sshcfg.render_block("kaggle", "old.example.com")
    cfgfile.write_text("Host *\n    User nobody\n\n" + old)
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfgfile)
    sshcfg.write("kaggle", "new.example.com")
    text = cfgfile.read_text()
    assert text.count(sshcfg.BEGIN) == 1 and "old.example.com" not in text
    assert text.startswith(sshcfg.BEGIN)
    assert text.index(sshcfg.END) < text.index("Host *") and "User nobody" in text


@pytest.mark.parametrize(
    "existing",
    ["", "\n\n", "AddKeysToAgent yes", "Match host foo\n    User bar", "host=lower\n  User x\n"],
)
def test_odd_configs_keep_their_content(tmp_path, monkeypatch, existing):
    cfgfile = tmp_path / "config"
    cfgfile.write_text(existing)
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfgfile)
    sshcfg.write("kaggle", "box.example.com")
    text = cfgfile.read_text()
    assert text.count(sshcfg.BEGIN) == 1
    for line in _lines(existing):
        assert line in text
    if _lines(existing) and existing.lstrip().lower().startswith(("match", "host")):
        assert text.startswith(sshcfg.BEGIN)
    assert sshcfg.current() == ("kaggle", "box.example.com")


def test_a_symlinked_config_stays_a_symlink(tmp_path, monkeypatch):
    """Dotfile managers link ~/.ssh/config; replacing the link loses the repo copy."""
    real = tmp_path / "dotfiles" / "ssh_config"
    real.parent.mkdir()
    real.write_text("Host other\n    User me\n")
    link = tmp_path / "config"
    link.symlink_to(real)
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", link)
    sshcfg.write("kaggle", "box.example.com")
    assert link.is_symlink() and sshcfg.BEGIN in real.read_text()


# --- keeping it in step with the settings -----------------------------------------


def _cfg(**kw):
    from kdev import config

    return config.Config(**kw)


def test_renaming_the_alias_rewrites_the_block(monkeypatch):
    """`kdev config set ssh-alias box` left `Host kaggle` behind; `kdev ssh` then
    asked ssh for a host the file did not have."""
    from kdev import session

    sshcfg.write("kaggle", "box.example.com")
    session.ssh_ready(_cfg(tunnel_hostname="box.example.com", ssh_host_alias="gpu"))
    assert sshcfg.current() == ("gpu", "box.example.com")
    assert sshcfg.SSH_CONFIG.read_text().count(sshcfg.BEGIN) == 1


def test_a_quick_tunnel_host_survives_an_alias_rename(monkeypatch):
    """Only the box knows a quick tunnel's hostname: it must not be replaced
    by the configured one, or by nothing."""
    from kdev import session

    sshcfg.write("kaggle", "random-words.trycloudflare.com")
    session.ssh_ready(_cfg(tunnel_hostname="", ssh_host_alias="gpu"))
    assert sshcfg.current() == ("gpu", "random-words.trycloudflare.com")
    # A named tunnel configured here does not override a box that is up
    # on a quick one -- unless the hostname setting itself just changed.
    session.ssh_ready(_cfg(tunnel_hostname="box.example.com", ssh_host_alias="gpu"))
    assert sshcfg.current() == ("gpu", "random-words.trycloudflare.com")
    session.ssh_ready(
        _cfg(tunnel_hostname="box.example.com", ssh_host_alias="gpu"), host_changed=True
    )
    assert sshcfg.current() == ("gpu", "box.example.com")


def test_ssh_ready_leaves_a_matching_block_alone(monkeypatch):
    from kdev import session

    sshcfg.write("kaggle", "box.example.com")
    before = sshcfg.SSH_CONFIG.stat().st_mtime_ns
    monkeypatch.setattr(sshcfg, "write", lambda *a, **k: pytest.fail("must not rewrite"))
    session.ssh_ready(_cfg(tunnel_hostname="box.example.com", ssh_host_alias="kaggle"))
    assert sshcfg.SSH_CONFIG.stat().st_mtime_ns == before


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["set", "ssh-alias", "gpu"], ("gpu", "box.example.com")),
        (["unset", "ssh-alias"], ("kaggle", "box.example.com")),
        (["set", "tunnel-hostname", "new.example.com"], ("kaggle", "new.example.com")),
    ],
)
def test_config_set_keeps_ssh_config_in_step(monkeypatch, argv, expected):
    from typer.testing import CliRunner

    from kdev import config
    from kdev.commands import settings

    cfg = config.Config(tunnel_hostname="box.example.com", ssh_host_alias="gpu")
    cfg.add("alice", config.Profile("alice"))
    if argv[0] == "set" and argv[1] == "tunnel-hostname":
        cfg.ssh_host_alias = "kaggle"
    config.save(cfg)
    sshcfg.write(cfg.ssh_host_alias, cfg.tunnel_hostname)

    result = CliRunner().invoke(settings.app, argv)
    assert result.exit_code == 0, result.output
    assert sshcfg.current() == expected
