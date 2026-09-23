from kdev import sshcfg


def test_ssh_block_replaces_itself_and_preserves_neighbours(tmp_path, monkeypatch):
    cfgfile = tmp_path / "config"
    cfgfile.write_text("Host github.com\n    User git\n\nHost other\n    User me\n")
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfgfile)
    monkeypatch.setattr(sshcfg, "KNOWN_HOSTS", tmp_path / "known_hosts.kdev")

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
    monkeypatch.setattr(sshcfg, "KNOWN_HOSTS", tmp_path / "kh")

    managed = tmp_path / "kdev bin" / "cloudflared"
    sshcfg.write("kaggle", "host.example.com", cloudflared_path=managed)
    text = cfgfile.read_text()
    assert f'ProxyCommand "{managed}" access ssh' in text


def test_ssh_block_presence_is_detectable(tmp_path, monkeypatch):
    """`kdev ssh` should say 'no session' rather than hand ssh a dead alias."""
    from kdev import sshcfg

    cfg = tmp_path / "config"
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", cfg)
    monkeypatch.setattr(sshcfg, "KNOWN_HOSTS", tmp_path / "known_hosts")
    assert not sshcfg.has_block()
    sshcfg.write("kaggle", "box.example.com")
    assert sshcfg.has_block()
    sshcfg.clear()
    assert not sshcfg.has_block()
