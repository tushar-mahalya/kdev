import json
import os
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from kdev import api, auth, config


def test_an_account_authenticates_with_a_bearer_token_only():
    """Kaggle rejects the legacy username+key on the endpoints kdev needs;
    a signed-out account must send nothing rather than something wrong."""
    from kdev.api import Creds

    assert Creds("u", "tok").headers == {"Authorization": "Bearer tok"}
    assert Creds("u").headers == {}


def test_login_does_not_clobber_another_profile(tmp_path, monkeypatch):
    """`kaggle auth login` always writes one shared file; profiles must not collide."""
    from kdev import auth

    shared = tmp_path / "credentials.json"
    shared.write_text(json.dumps({"refresh_token": "alice-rt", "username": "alice"}))
    monkeypatch.setattr(auth, "KAGGLE_CREDENTIALS", shared)
    monkeypatch.setattr(auth, "creds_dir", lambda: tmp_path / "creds")

    def fake_login(argv, **kw):
        shared.write_text(json.dumps({"refresh_token": "bob-rt", "username": "bob"}))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(auth.subprocess, "run", fake_login)
    assert auth.login("bob") == "bob"

    stored = json.loads(auth.creds_path("bob").read_text())
    assert stored["refresh_token"] == "bob-rt"
    # The user's own CLI login must be left exactly as it was.
    assert json.loads(shared.read_text())["username"] == "alice"


def test_login_rejects_credentials_without_a_refresh_token(tmp_path, monkeypatch):
    """Access tokens expire in 12h; without a refresh token the profile dies."""
    from kdev import auth

    shared = tmp_path / "credentials.json"
    monkeypatch.setattr(auth, "KAGGLE_CREDENTIALS", shared)
    monkeypatch.setattr(auth, "creds_dir", lambda: tmp_path / "creds")
    monkeypatch.setattr(
        auth.subprocess,
        "run",
        lambda *a, **k: (
            shared.write_text(json.dumps({"username": "bob"})),
            type("R", (), {"returncode": 0})(),
        )[1],
    )

    with pytest.raises(auth.AuthError, match="refresh token"):
        auth.login("bob")


def test_profile_without_login_yields_no_token(tmp_path, monkeypatch):
    from kdev import auth

    monkeypatch.setattr(auth, "creds_dir", lambda: tmp_path / "creds")
    cfg = config.Config(profiles={"me": config.Profile("u")}, active="me")
    assert cfg.profile().creds.token == ""


# --- access tokens --------------------------------------------------------------


def _stored(name: str, *, expires_in: timedelta | None, token: str = "old-token") -> dict:
    data = {
        "refresh_token": f"rt-{name}",
        "access_token": token,
        "access_token_expiration": (
            (datetime.now(UTC) + expires_in).isoformat() if expires_in else ""
        ),
        "username": name,
        "scopes": ["resources.admin:*"],
    }
    path = auth.creds_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return data


def _mint_counter(monkeypatch, expires_in: int = 43200) -> list[dict]:
    """Answer GenerateAccessToken; record each request made."""
    minted: list[dict] = []

    def call(creds, method, payload=None, service=api.KERNELS, attempts=3):
        assert (service, method) == (auth.ACCOUNTS, "GenerateAccessToken")
        assert not creds.token, "minting must not send another account's token"
        minted.append(payload or {})
        return {"token": f"new-token-{len(minted)}", "expiresIn": expires_in}

    monkeypatch.setattr(api, "call", call)
    return minted


def test_a_token_is_minted_once_per_process_and_kept(monkeypatch):
    """`kdev up` reads an account's creds ~20 times; each read used to mint."""
    _stored("alice", expires_in=timedelta(hours=-1))
    minted = _mint_counter(monkeypatch)

    tokens = {auth.access_token("alice") for _ in range(20)}
    assert tokens == {"new-token-1"} and len(minted) == 1
    assert minted[0]["refreshToken"] == "rt-alice"
    assert minted[0]["expirationDuration"] == "43200s"


def test_a_refreshed_token_is_saved_to_the_profile_for_the_next_process(monkeypatch):
    _stored("alice", expires_in=None)
    _mint_counter(monkeypatch)
    auth.access_token("alice")

    path = auth.creds_path("alice")
    saved = json.loads(path.read_text())
    assert saved["access_token"] == "new-token-1"
    assert saved["refresh_token"] == "rt-alice" and saved["scopes"] == ["resources.admin:*"]
    left = datetime.fromisoformat(saved["access_token_expiration"]) - datetime.now(UTC)
    assert timedelta(hours=11) < left <= timedelta(hours=12)
    assert oct(path.stat().st_mode)[-3:] == "600"

    # A new process (an empty cache) uses the saved token without a round trip.
    monkeypatch.setattr(auth, "_cache", {})
    monkeypatch.setattr(api, "call", lambda *a, **k: pytest.fail("must not mint again"))
    assert auth.access_token("alice") == "new-token-1"


def test_a_stored_token_with_time_left_is_used_as_is(monkeypatch):
    _stored("alice", expires_in=timedelta(hours=5), token="still-good")
    monkeypatch.setattr(api, "call", lambda *a, **k: pytest.fail("must not mint"))
    assert auth.access_token("alice") == "still-good"


@pytest.mark.parametrize(
    "left",
    [timedelta(minutes=59), timedelta(minutes=29), timedelta(minutes=1), timedelta(hours=-3)],
)
def test_a_token_near_or_past_expiry_is_refreshed(monkeypatch, left):
    """kagglesdk's own check was backwards: it only refreshed 30 minutes
    *after* expiry, so a token could lapse in the middle of a session start.
    kdev refreshes while an hour or less is left."""
    _stored("alice", expires_in=left)
    minted = _mint_counter(monkeypatch)
    assert auth.access_token("alice") == "new-token-1" and len(minted) == 1


def test_a_cached_token_is_refreshed_once_it_nears_expiry(monkeypatch):
    _stored("alice", expires_in=None)
    # Granted for less than the refresh margin: stale as soon as it is cached.
    minted = _mint_counter(monkeypatch, expires_in=int(auth.REFRESH_MARGIN.total_seconds()) - 60)
    assert auth.access_token("alice") == "new-token-1"
    assert auth.access_token("alice") == "new-token-2"
    assert len(minted) == 2


def test_minting_never_touches_the_official_cli_login(monkeypatch):
    """kagglesdk's refresh saved every new token to ~/.kaggle/credentials.json,
    which is the user's own `kaggle` CLI login and possibly another account."""
    from pathlib import Path

    shared = auth.KAGGLE_CREDENTIALS
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(json.dumps({"refresh_token": "mine", "username": "me"}))
    before = (shared.read_text(), shared.stat().st_mtime_ns)

    _stored("alice", expires_in=None)
    _stored("bob", expires_in=None)
    _mint_counter(monkeypatch)
    auth.access_token("alice")
    auth.access_token("bob")

    assert (shared.read_text(), shared.stat().st_mtime_ns) == before
    assert sorted(p.name for p in (Path.home() / ".kaggle").iterdir()) == ["credentials.json"]


def test_concurrent_reads_mint_once_and_leave_valid_json(monkeypatch):
    """quota.rows reads every account at once, and threads share accounts."""
    _stored("alice", expires_in=None)
    gate = threading.Event()
    minted = []

    def slow_call(creds, method, payload=None, service=api.KERNELS, attempts=3):
        minted.append(1)
        gate.wait(2)
        return {"token": "new", "expiresIn": 43200}

    monkeypatch.setattr(api, "call", slow_call)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(auth.access_token("alice")))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(5)
    assert results == ["new"] * 8 and len(minted) == 1
    assert json.loads(auth.creds_path("alice").read_text())["access_token"] == "new"
    assert [p.name for p in auth.creds_dir().iterdir()] == ["alice.json"], "no temp files left"


def test_a_failed_write_leaves_the_old_file_whole(monkeypatch):
    """Written through a temp file: a crash mid-write must not sign anyone out."""
    original = _stored("alice", expires_in=None)
    _mint_counter(monkeypatch)

    def crash(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)
    monkeypatch.setattr(
        type(auth.creds_path("alice")), "replace", lambda self, dst: crash(self, dst)
    )
    # The token still works for this process; the file is untouched.
    assert auth.access_token("alice") == "new-token-1"
    assert json.loads(auth.creds_path("alice").read_text()) == original
    assert [p.name for p in auth.creds_dir().iterdir()] == ["alice.json"]


def test_a_relogin_is_picked_up_without_restarting(monkeypatch):
    """`kdev account add <name>` replaces the file; the cached token is someone's old one."""
    _stored("alice", expires_in=timedelta(hours=5), token="before")
    assert auth.access_token("alice") == "before"
    _stored("alice", expires_in=timedelta(hours=5), token="after-relogin")
    assert auth.access_token("alice") == "after-relogin"


def test_being_offline_is_not_being_signed_out(monkeypatch):
    """Offline used to crash with "unexpected error" (requests' ConnectionError
    escaped), or read as signed out. It must be its own, clear AuthError."""
    _stored("alice", expires_in=None)

    def offline(url, **kw):
        raise httpx.ConnectError("[Errno 61] Connection refused")

    monkeypatch.setattr(api.httpx, "post", offline)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    with pytest.raises(auth.AuthUnavailable, match="Could not reach Kaggle"):
        auth.access_token("alice")

    cfg = config.Config()
    cfg.add("alice", config.Profile("alice"))
    with pytest.raises(auth.AuthError):
        _ = cfg.profile("alice").creds


@pytest.mark.parametrize("status", [500, 503])
def test_kaggle_failing_to_mint_is_unavailable_not_signed_out(monkeypatch, status):
    _stored("alice", expires_in=None)
    monkeypatch.setattr(api.httpx, "post", lambda url, **kw: httpx.Response(status, text="down"))
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    with pytest.raises(auth.AuthUnavailable):
        auth.access_token("alice")


def test_a_rejected_refresh_token_reads_as_signed_out(monkeypatch):
    _stored("alice", expires_in=None)
    monkeypatch.setattr(api.httpx, "post", lambda url, **kw: httpx.Response(401, text="no"))
    with pytest.raises(auth.AuthError, match="kdev account add alice") as e:
        auth.access_token("alice")
    assert not isinstance(e.value, auth.AuthUnavailable)

    cfg = config.Config()
    cfg.add("alice", config.Profile("alice"))
    assert cfg.profile("alice").creds.token == "", "the call it is for then says: not signed in"


def test_the_quota_table_shows_an_offline_account_as_a_row_not_a_crash(monkeypatch):
    from kdev import quota

    cfg = config.Config()
    for name in ("alice", "bob", "carol"):
        cfg.add(name, config.Profile(name))
        _stored(name, expires_in=None)

    def offline(url, **kw):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(api.httpx, "post", offline)
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    rows = quota.rows(cfg)
    assert [r.name for r in rows] == ["alice", "bob", "carol"]
    assert {r.error for r in rows} == {quota.OFFLINE}


def test_the_quota_table_survives_any_transport_error(monkeypatch):
    from kdev import quota

    cfg = config.Config()
    cfg.add("alice", config.Profile("alice"))
    monkeypatch.setattr(auth, "signed_in", lambda name: True)
    monkeypatch.setattr(auth, "access_token", lambda name: "t")

    def boom(creds):
        raise httpx.RemoteProtocolError("peer closed connection")

    monkeypatch.setattr(api, "quota", boom)
    [row] = quota.rows(cfg)
    assert "peer closed" in row.error
