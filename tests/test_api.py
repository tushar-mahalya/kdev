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


def test_historical_status_uses_the_requested_version(monkeypatch):
    seen = {}

    def fake_call(creds, method, payload=None, service=api.KERNELS, attempts=3):
        seen.update(payload or {})
        return {"status": "COMPLETE"}

    monkeypatch.setattr(api, "call", fake_call)
    assert api.session_status(api.Creds("u"), "alice/box", "v7") == {"status": "COMPLETE"}
    assert seen == {"userName": "alice", "kernelSlug": "box", "versionLabel": "v7"}


def test_get_kernel_metadata_is_pinned_to_the_selected_version(monkeypatch):
    seen = []
    monkeypatch.setattr(
        api,
        "call",
        lambda c, method, payload: (
            seen.append((method, payload)) or {"metadata": {"machineShape": "NvidiaTeslaP100"}}
        ),
    )
    assert api.get_kernel(api.Creds("u"), "alice/box", "v7")["machineShape"] == "NvidiaTeslaP100"
    assert seen == [("GetKernel", {"userName": "alice", "kernelSlug": "box", "versionLabel": "v7"})]


def test_save_kernel_never_retries_an_uncertain_run_creation(monkeypatch):
    import httpx

    calls = []

    def post(url, **kw):
        calls.append(url)
        raise httpx.ReadTimeout("response lost after accepting the run")

    monkeypatch.setattr(api.httpx, "post", post)
    with pytest.raises(api.KaggleError, match="could not reach"):
        api.save_kernel(
            api.Creds("u"),
            slug="a/b",
            title="b",
            source="pass",
            machine_shape=None,
            timeout_seconds=600,
        )
    assert len(calls) == 1


def test_explicit_api_rejection_preserves_http_status(monkeypatch):
    import httpx

    monkeypatch.setattr(
        api.httpx, "post", lambda *a, **kw: httpx.Response(200, json={"error": "database conflict"})
    )
    with pytest.raises(api.KaggleError) as error:
        api.call(api.Creds("u"), "SaveKernel")
    assert error.value.status == 200


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("text/event-stream", b'data: {"text":"first"}\n\ndata: END_OF_LOG\n\ndata: ignored\n'),
        ("application/json", b'[{"text":"first"}]'),
    ],
)
def test_live_and_persisted_logs_use_version_specific_request(monkeypatch, content_type, body):
    from contextlib import contextmanager

    import httpx

    seen = []

    @contextmanager
    def stream(method, url, **kw):
        seen.append(kw["json"])
        yield httpx.Response(200, headers={"content-type": content_type}, content=body)

    monkeypatch.setattr(api.httpx, "stream", stream)
    assert list(api.stream_logs(api.Creds("u"), "alice/box", version="v7")) == ["first"]
    assert seen == [
        {
            "userName": "alice",
            "kernelSlug": "box",
            "waitForLogsUrlSeconds": 300,
            "versionLabel": "v7",
        }
    ]


def test_a_notebook_that_has_never_run_is_a_status_not_an_error(monkeypatch):
    """Measured on a notebook made in the Kaggle editor: GetKernelSessionStatus
    answers 404 "No runs found for this kernel". `kdev up` treated that as
    fatal; it only means nothing is running."""

    def never_run(creds, method, payload=None, service=api.KERNELS, attempts=3):
        raise api.KaggleError(f"{method}: No runs found for this kernel. (HTTP 404)", 404)

    monkeypatch.setattr(api, "call", never_run)
    assert api.session_status(api.Creds("u"), "a/b") == {"status": api.NEVER_RUN}
    assert api.NEVER_RUN not in api.LIVE_STATES

    def denied(creds, method, payload=None, service=api.KERNELS, attempts=3):
        raise api.KaggleError(f"{method}: Permission denied (HTTP 403)", 403)

    monkeypatch.setattr(api, "call", denied)
    with pytest.raises(api.KaggleError):
        api.session_status(api.Creds("u"), "a/b")  # other failures still surface


def test_a_prefix_listing_stops_paging_once_past_the_prefix(monkeypatch):
    pages = {
        None: {
            "files": [{"fileName": n, "url": "u"} for n in (".kdev/a", ".kdev/state.json", "b")],
            "nextPageToken": "p2",
        },
        "p2": {"files": [{"fileName": "c", "url": "u"}]},
    }
    seen = []

    def call(creds, method, payload, **kw):
        seen.append(payload.get("pageToken"))
        return pages[payload.get("pageToken")]

    monkeypatch.setattr(api, "call", call)
    names = [f["name"] for f in api.session_output(api.Creds("u"), "a/b", "v1", prefix=".kdev/")]
    assert seen == [None] and ".kdev/state.json" in names


def test_a_prefix_listing_keeps_paging_when_the_prefix_may_continue(monkeypatch):
    pages = {
        None: {"files": [{"fileName": ".kdev/a", "url": "u"}], "nextPageToken": "p2"},
        "p2": {
            "files": [{"fileName": ".kdev/state.json", "url": "u"}, {"fileName": "b", "url": "u"}]
        },
    }
    monkeypatch.setattr(api, "call", lambda c, m, p, **kw: pages[p.get("pageToken")])
    names = [f["name"] for f in api.session_output(api.Creds("u"), "a/b", "v1", prefix=".kdev/")]
    assert ".kdev/state.json" in names


def test_an_unsorted_page_is_never_trusted_to_end_the_prefix(monkeypatch):
    pages = {
        None: {
            "files": [{"fileName": n, "url": "u"} for n in ("z", ".kdev/a")],
            "nextPageToken": "p2",
        },
        "p2": {"files": [{"fileName": ".kdev/state.json", "url": "u"}]},
    }
    monkeypatch.setattr(api, "call", lambda c, m, p, **kw: pages[p.get("pageToken")])
    names = [f["name"] for f in api.session_output(api.Creds("u"), "a/b", "v1", prefix=".kdev/")]
    assert ".kdev/state.json" in names


def test_rate_limiting_is_waited_out_with_retry_after(monkeypatch):
    import httpx

    answers = iter(
        [httpx.Response(429, headers={"retry-after": "2"})] * 3
        + [httpx.Response(200, json={"ok": 1})]
    )
    slept = []
    monkeypatch.setattr(api.httpx, "post", lambda *a, **kw: next(answers))
    monkeypatch.setattr(api.time, "sleep", slept.append)
    assert api.call(api.Creds("u"), "GetKernel", {}) == {"ok": 1}
    assert slept == [2.0, 2.0, 2.0]


def test_a_call_that_must_not_repeat_is_never_retried_even_when_limited(monkeypatch):
    import httpx

    calls = []
    monkeypatch.setattr(api.httpx, "post", lambda *a, **kw: calls.append(1) or httpx.Response(429))
    with pytest.raises(api.KaggleError):
        api.call(api.Creds("u"), "SaveKernel", {}, attempts=1)
    assert len(calls) == 1


def test_a_prefix_read_falls_back_to_full_pages_when_the_prefix_sorts_late(monkeypatch):
    """A `.git/` at the root sorts before `.kdev/`: fifty names at a time
    would take dozens of calls, so the read restarts at the full page size."""
    names = [f".git/objects/{i:04d}" for i in range(900)] + [".kdev/state.json", "z.py"]
    calls = []

    def call(creds, method, payload, **kw):
        size, start = payload["pageSize"], int(payload.get("pageToken") or 0)
        calls.append(size)
        page = names[start : start + size]
        nxt = start + size
        return {
            "files": [{"fileName": n, "url": "u"} for n in page],
            "nextPageToken": str(nxt) if nxt < len(names) else "",
        }

    monkeypatch.setattr(api, "call", call)
    got = [f["name"] for f in api.session_output(api.Creds("u"), "a/b", "v1", prefix=".kdev/")]
    assert ".kdev/state.json" in got
    assert calls == [api.PREFIX_PAGE, api.OUTPUT_PAGE, api.OUTPUT_PAGE]
