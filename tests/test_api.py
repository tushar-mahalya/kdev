import pytest

from kdev import api
from kdev.quota import secs as _secs
from kdev.ui import fmt_hours as _fmt_hours


def test_duration_parsing_handles_protobuf_and_numbers():
    assert _secs("108000s") == 108000
    assert _secs(3600) == 3600
    assert _secs(None) == 0
    assert _fmt_hours(108000) == "30.0h"


def test_gpu_shapes_cover_the_documented_values():
    assert api.SHAPES["none"] is None
    assert api.SHAPES["t4"] == "NvidiaTeslaT4"
    assert api.SHAPES["p100"] == "NvidiaTeslaP100"
    assert api.SHAPES["tpu"] == "Tpu1VmV38"


def test_a_transient_5xx_is_retried_rather_than_killing_the_run(monkeypatch):
    from kdev import api

    calls = []

    class R:
        def __init__(self, code):
            self.status_code, self.content, self.text = code, b"{}", "{}"

        def json(self):
            return {}

    def post(url, **kw):
        calls.append(url)
        return R(503 if len(calls) < 3 else 200)

    monkeypatch.setattr(api.httpx, "post", post)
    monkeypatch.setattr(api.time, "sleep", lambda _s: None)
    assert api.call(api.Creds("u"), "GetKernel") == {}
    assert len(calls) == 3


def test_a_401_is_not_retried_and_says_how_to_fix_it(monkeypatch):
    from kdev import api

    calls = []

    class R:
        status_code, content, text = 401, b"", ""

        def json(self):
            return {}

    monkeypatch.setattr(api.httpx, "post", lambda url, **kw: (calls.append(url), R())[1])
    monkeypatch.setattr(api.time, "sleep", lambda _s: None)
    with pytest.raises(api.KaggleError) as e:
        api.call(api.Creds("u"), "GetKernel")
    assert len(calls) == 1
    assert e.value.status == 401 and "not signed in" in str(e.value)


def test_every_offered_accelerator_is_a_real_shape():
    """The picker must not offer an entitlement-gated shape that answers 403."""
    from kdev import api, ui

    for key, _label, _desc in ui.GPU_CHOICES:
        assert key in api.COMMON_SHAPES, key
    assert set(api.COMMON_SHAPES) <= set(api.SHAPES)


def test_live_states_are_the_ones_that_still_cost_quota():
    from kdev import api

    assert {"RUNNING", "QUEUED"} == api.LIVE_STATES
    assert "COMPLETE" not in api.LIVE_STATES
