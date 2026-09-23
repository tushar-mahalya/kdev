from pathlib import Path

import pytest

from kdev import bootstrap, config


def test_proxy_command_quotes_paths_with_spaces():
    """The ProxyCommand is run through /bin/sh; app-support paths have spaces."""
    from kdev import cloudflared

    plain = cloudflared.proxy_command(Path("/usr/local/bin/cloudflared"))
    assert plain == "/usr/local/bin/cloudflared access ssh --hostname %h"

    spaced = cloudflared.proxy_command(Path("/Users/a b/Library/App Support/cloudflared"))
    assert spaced.startswith('"/Users/a b/Library/App Support/cloudflared"')
    assert spaced.endswith("access ssh --hostname %h")


def test_proxy_command_never_touches_the_network(monkeypatch):
    """Rendering an ssh config must not trigger a 19 MB download."""
    from kdev import cloudflared

    monkeypatch.setattr(cloudflared, "find", lambda: None)
    monkeypatch.setattr(
        cloudflared, "install", lambda *a, **k: pytest.fail("install() called during render")
    )
    assert cloudflared.proxy_command() == "cloudflared access ssh --hostname %h"


def test_every_supported_platform_maps_to_a_real_release_asset():
    """Asset names must match Cloudflare's actual release filenames."""
    from kdev import cloudflared

    known = {
        "cloudflared-darwin-arm64.tgz",
        "cloudflared-darwin-amd64.tgz",
        "cloudflared-linux-amd64",
        "cloudflared-linux-arm64",
        "cloudflared-linux-arm",
        "cloudflared-windows-amd64.exe",
    }
    assert set(cloudflared._ASSETS.values()) <= known


def test_credentials_without_id_or_hostname_is_rejected():
    """A creds tunnel with no hostname would start and route nothing."""
    with pytest.raises(ValueError):
        bootstrap.render(
            ssh_public_key="k",
            tunnel_credentials="{}",
            tunnel_id="",
            tunnel_hostname="h",
            hold_seconds=60,
        )
    with pytest.raises(ValueError):
        bootstrap.render(
            ssh_public_key="k",
            tunnel_credentials="{}",
            tunnel_id="u",
            tunnel_hostname="",
            hold_seconds=60,
        )


def test_credentials_validates_required_keys(monkeypatch, tmp_path):
    """A truncated credentials file must fail here, not on the Kaggle box."""
    from kdev import tunnel as t

    def fake_run(args, **kw):
        path = Path(args[args.index("--cred-file") + 1])
        path.write_text('{"AccountTag": "a"}')  # missing TunnelSecret/TunnelID
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(t, "_run", fake_run)
    with pytest.raises(t.TunnelError, match="TunnelSecret"):
        t.credentials("kdev")


def test_route_dns_treats_an_existing_record_as_success(monkeypatch):
    """Re-running setup on an already-routed hostname is normal."""
    from kdev import tunnel as t

    monkeypatch.setattr(
        t,
        "_run",
        lambda *a, **k: type(
            "R", (), {"returncode": 1, "stdout": "", "stderr": "record already exists"}
        )(),
    )
    t.route_dns("kdev", "kaggle.example.com")  # must not raise

    monkeypatch.setattr(
        t,
        "_run",
        lambda *a, **k: type(
            "R", (), {"returncode": 1, "stdout": "", "stderr": "zone not found"}
        )(),
    )
    with pytest.raises(t.TunnelError):
        t.route_dns("kdev", "kaggle.example.com")


def test_hostname_validation_rejects_a_bare_label():
    """`kdev` next to a `Tunnel name: kdev` prompt reads plausible but is unroutable."""
    from kdev import tunnel as t

    with pytest.raises(t.TunnelError, match="bare label"):
        t.validate_hostname("kdev", "example.com")


@pytest.mark.parametrize("bad", ["", "-lead.example.com", "trail-.example.com", "a..b.com"])
def test_hostname_validation_rejects_malformed_names(bad):
    from kdev import tunnel as t

    with pytest.raises(t.TunnelError):
        t.validate_hostname(bad, "example.com")


def test_hostname_must_live_inside_the_authorised_zone():
    from kdev import tunnel as t

    with pytest.raises(t.TunnelError, match="not inside your Cloudflare zone"):
        t.validate_hostname("kaggle.someone-else.com", "example.com")
    # A lookalike suffix must not pass: notexample.com does not end in .example.com
    with pytest.raises(t.TunnelError):
        t.validate_hostname("kaggle.notexample.com", "example.com")


def test_hostname_validation_normalises_case_and_trailing_dot():
    from kdev import tunnel as t

    assert t.validate_hostname("Kaggle.Example.COM.", "example.com") == "kaggle.example.com"
    assert t.validate_hostname("example.com", "example.com") == "example.com"


def test_zone_name_returns_empty_without_a_cert(monkeypatch, tmp_path):
    """No cert, no network call — setup must degrade to asking, not crash."""
    from kdev import tunnel as t

    monkeypatch.setattr(t, "CERT", tmp_path / "missing.pem")
    assert t.zone_name() == ""


@pytest.mark.parametrize("bad", ["", "-x", "x-", "a.b", "has space", "UPPER!"])
def test_box_name_must_be_a_single_dns_label(bad):
    """The name becomes a subdomain, so it has to be a legal label."""
    from kdev import tunnel as t

    with pytest.raises(t.TunnelError):
        t.validate_label(bad)


def test_box_name_normalises_case():
    from kdev import tunnel as t

    assert t.validate_label("KDev") == "kdev"
    assert t.validate_label("box-2") == "box-2"


def _rec(content):
    return {"type": "CNAME", "name": "kdev.example.com", "content": content}


def _recorder(seen):
    """Capture the argv cloudflared would be called with, and report success."""

    def run(args, **kw):
        seen["args"] = args
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    return run


def test_route_dns_reuses_a_record_that_already_points_at_the_tunnel(monkeypatch):
    """The bug: --overwrite-dns ran unconditionally and rewrote a correct record."""
    from kdev import tunnel as t

    monkeypatch.setattr(t, "dns_record", lambda h: _rec("uuid-1.cfargotunnel.com"))
    monkeypatch.setattr(t, "_run", lambda *a, **k: pytest.fail("must not touch DNS"))
    assert t.route_dns("kdev", "kdev.example.com", "uuid-1") == "reused"


def test_route_dns_creates_without_overwrite_when_absent(monkeypatch):
    from kdev import tunnel as t

    seen = {}
    monkeypatch.setattr(t, "dns_record", lambda h: None)
    monkeypatch.setattr(t, "_run", _recorder(seen))
    assert t.route_dns("kdev", "kdev.example.com", "uuid-1") == "created"
    assert "--overwrite-dns" not in seen["args"], "nothing to overwrite"


def test_route_dns_will_not_clobber_someone_elses_record_without_consent(monkeypatch):
    """A hostname pointing elsewhere must not be silently repointed."""
    from kdev import tunnel as t

    monkeypatch.setattr(t, "dns_record", lambda h: _rec("8.8.8.8"))
    monkeypatch.setattr(t, "_run", lambda *a, **k: pytest.fail("must not touch DNS"))
    with pytest.raises(t.TunnelError, match="already points at"):
        t.route_dns("kdev", "kdev.example.com", "uuid-1", on_conflict=lambda r: False)


def test_route_dns_repoints_when_the_user_agrees(monkeypatch):
    from kdev import tunnel as t

    seen = {}
    monkeypatch.setattr(t, "dns_record", lambda h: _rec("other.cfargotunnel.com"))
    monkeypatch.setattr(t, "_run", _recorder(seen))
    assert (
        t.route_dns("kdev", "kdev.example.com", "uuid-1", on_conflict=lambda r: True) == "repointed"
    )
    assert "--overwrite-dns" in seen["args"]


def test_is_configured_requires_tunnel_and_matching_dns(monkeypatch):
    from kdev import tunnel as t

    cfg = config.Config(
        tunnel_credentials="{}", tunnel_id="uuid-1", tunnel_hostname="kdev.example.com"
    )
    monkeypatch.setattr(t, "logged_in", lambda: True)
    monkeypatch.setattr(t, "list_tunnels", lambda: [{"id": "uuid-1"}])
    monkeypatch.setattr(t, "dns_record", lambda h: _rec("uuid-1.cfargotunnel.com"))
    assert t.is_configured(cfg) is True

    # Tunnel deleted on the Cloudflare side -> must re-provision, not assume.
    monkeypatch.setattr(t, "list_tunnels", lambda: [])
    assert t.is_configured(cfg) is False

    monkeypatch.setattr(t, "list_tunnels", lambda: [{"id": "uuid-1"}])
    monkeypatch.setattr(t, "dns_record", lambda h: _rec("stale.cfargotunnel.com"))
    assert t.is_configured(cfg) is False


def test_existing_tunnel_is_matched_only_when_dns_points_at_it(monkeypatch):
    """Offering a reuse on a stale tunnel would hand back an unreachable box."""
    from kdev import tunnel as t

    monkeypatch.setattr(
        t,
        "list_tunnels",
        lambda: [
            {"name": "kdev", "id": "uuid-1"},
            {"name": "stale", "id": "uuid-2"},
            {"name": "nodns", "id": "uuid-3"},
        ],
    )
    records = {
        "kdev.example.com": _rec("uuid-1.cfargotunnel.com"),
        "stale.example.com": _rec("someone-else.cfargotunnel.com"),
    }
    monkeypatch.setattr(t, "dns_record", lambda h: records.get(h))

    found = t.existing_for_zone("example.com")
    assert [f["name"] for f in found] == ["kdev"]
    assert found[0]["hostname"] == "kdev.example.com"


def test_existing_for_zone_is_empty_without_a_zone(monkeypatch):
    from kdev import tunnel as t

    monkeypatch.setattr(t, "list_tunnels", lambda: pytest.fail("should not query"))
    assert t.existing_for_zone("") == []


def test_checkpointing_is_skipped_when_no_remote_is_configured():
    """clone_repo returns None without a remote, so the guard must read it."""
    src = bootstrap.render(ssh_public_key="k", tunnel_token="", tunnel_hostname="", hold_seconds=60)
    assert "if repo and" in src  # repo is None -> no push attempts, no log noise
