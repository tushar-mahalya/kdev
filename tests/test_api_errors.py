"""Kaggle answering with something other than what the API promises."""

from contextlib import contextmanager

import httpx
import pytest

from kdev import api


@pytest.mark.parametrize(
    "body",
    [b'["a", "list"]', b'"a string"', b"42", b"null", b"<html>bad gateway</html>", b""],
)
def test_an_error_body_of_any_shape_becomes_a_kaggle_error(monkeypatch, body):
    """A JSON list used to raise AttributeError: "unexpected error", not a message."""
    monkeypatch.setattr(
        api.httpx,
        "post",
        lambda url, **kw: httpx.Response(
            403, content=body, headers={"content-type": "application/json"}
        ),
    )
    with pytest.raises(api.KaggleError) as e:
        api.call(api.Creds("u", "t"), "GetKernel", {})
    assert e.value.status == 403 and "HTTP 403" in str(e.value)


def test_kaggles_own_error_message_is_kept(monkeypatch):
    monkeypatch.setattr(
        api.httpx,
        "post",
        lambda url, **kw: httpx.Response(403, json={"error": {"message": "Permission denied"}}),
    )
    with pytest.raises(api.KaggleError, match="Permission denied"):
        api.call(api.Creds("u", "t"), "GetKernel", {})


def _logs(monkeypatch, content_type: str, body: bytes) -> list[str]:
    @contextmanager
    def stream(method, url, **kw):
        yield httpx.Response(200, headers={"content-type": content_type}, content=body)

    monkeypatch.setattr(api.httpx, "stream", stream)
    return list(api.stream_logs(api.Creds("u"), "alice/box"))


def test_a_persisted_log_that_is_not_json_is_a_kaggle_error(monkeypatch):
    """json.loads used to raise JSONDecodeError straight out of `kdev logs`."""
    with pytest.raises(api.KaggleError, match="not JSON"):
        _logs(monkeypatch, "application/json", b"<html>gateway timeout</html>")


def test_a_persisted_log_that_is_one_object_is_still_shown(monkeypatch):
    assert _logs(monkeypatch, "application/json", b'{"data": "only line"}') == ["only line"]
