"""The test environment itself: nothing may reach the developer's machine."""

from pathlib import Path

import httpx
import pytest

from kdev import api, auth, cloudflared, config, sshcfg


def test_paths_point_into_a_throwaway_home():
    home = Path.home()
    assert "pytest" in str(home) or "tmp" in str(home).lower()
    for path in (config.CONFIG_DIR, sshcfg.SSH_CONFIG, auth.KAGGLE_CREDENTIALS):
        assert path.is_relative_to(home), path


def test_cloudflared_is_never_the_real_one():
    """`kdev up` tests would otherwise download a 39 MB binary."""
    found = cloudflared.find()
    assert found is not None and not found.exists()


def test_the_real_network_is_off_limits():
    with pytest.raises(pytest.fail.Exception, match="real network"):
        api.call(api.Creds("u", "t"), "GetKernel", {}, attempts=1)
    with pytest.raises(pytest.fail.Exception, match="real network"):
        httpx.get("https://example.com")
