import json

import pytest

from kdev import config


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
