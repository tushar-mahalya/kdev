"""Every test runs against a throwaway home, with no network.

The paths kdev writes to are computed at import, so pointing $HOME elsewhere
is not enough on its own: the module attributes are moved too. Without this a
test that forgets one fixture writes to the developer's real ~/.ssh/config or
~/.config/kdev, or downloads the real cloudflared.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path

import httpx
import pytest
from rich.style import Style

#: What `cloudflared.find` answers in tests. Never executed.
FAKE_CLOUDFLARED = Path("/opt/kdev-test/bin/cloudflared")


#: Loopback stays open: some tests serve a canned response from a local socket.
LOCAL = frozenset({"127.0.0.1", "localhost", "::1"})


def _no_network(url: str):
    # pytest.fail raises a BaseException: kdev's own `except Exception` and
    # retry loops cannot swallow it and report an "offline" result instead.
    pytest.fail(f"a test reached the real network ({url}); monkeypatch the call instead")


def _guard(real, url_of):
    def guarded(*args, **kwargs):
        url = url_of(*args, **kwargs)
        if urllib.parse.urlsplit(url).hostname not in LOCAL:
            _no_network(url)
        return real(*args, **kwargs)

    return guarded


def _request_url(_self, request, *args, **kwargs) -> str:
    return str(request.url)


def _urlopen_url(url, *args, **kwargs) -> str:
    return url.full_url if isinstance(url, urllib.request.Request) else str(url)


@pytest.fixture(autouse=True)
def _isolated(tmp_path_factory, monkeypatch):
    from kdev import auth, cloudflared, config, sshcfg

    home = tmp_path_factory.mktemp("home")
    config_dir = home / ".config" / "kdev"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KDEV_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KDEV_DATA_DIR", str(home / ".local" / "share" / "kdev"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(auth, "KAGGLE_CREDENTIALS", home / ".kaggle" / "credentials.json")
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", home / ".ssh" / "config")
    monkeypatch.setattr(cloudflared, "find", lambda: FAKE_CLOUDFLARED)
    monkeypatch.setattr(auth, "_cache", {})
    # Rich memoises parsed styles, and each Style keeps the ANSI codes of the
    # first colour system it rendered for. A test that renders through a
    # 16-colour console (Typer forces one under GITHUB_ACTIONS) would otherwise
    # leave truecolor tests seeing `36` where `#00b8d4` should be.
    Style.parse.cache_clear()
    Style._add.cache_clear()

    monkeypatch.setattr(httpx.Client, "send", _guard(httpx.Client.send, _request_url))
    monkeypatch.setattr(httpx.AsyncClient, "send", _guard(httpx.AsyncClient.send, _request_url))
    monkeypatch.setattr(urllib.request, "urlopen", _guard(urllib.request.urlopen, _urlopen_url))
    try:
        import requests
    except ImportError:  # pragma: no cover - kagglesdk brings it
        pass
    else:
        monkeypatch.setattr(requests.Session, "send", _guard(requests.Session.send, _request_url))
    return home
