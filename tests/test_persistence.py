import pytest

from kdev import api


def test_mirror_takes_no_notebook_and_so_cannot_be_keyed_to_one():
    """Keying the mirror to the notebook gave every new notebook an empty
    workspace, which is indistinguishable from the sync being broken. The
    parameter is gone rather than ignored, so it cannot come back by accident."""
    import inspect

    from kdev import persistence as w

    for fn in (w.mirror_dir, w.contents):
        assert not inspect.signature(fn).parameters, fn.__name__
    assert "notebook" not in inspect.signature(w.push).parameters
    assert "notebook" not in inspect.signature(w.pull).parameters


def test_mirror_excludes_kdev_and_kaggle_noise():
    """Syncing these back would overwrite the next session's own runtime files."""
    from kdev import persistence as w

    for noise in (
        "cloudflared.log",
        ".kdev-session",
        "__notebook__.ipynb",
        ".kdev-stop",
        "__pycache__",
    ):
        assert noise in w.EXCLUDES


def test_push_is_a_noop_when_nothing_has_been_saved(tmp_path, monkeypatch):
    """A first run must not fail just because the mirror is empty."""
    from kdev import persistence as w

    monkeypatch.setattr(w, "mirror_dir", lambda: tmp_path / "empty")
    monkeypatch.setattr(w, "_rsync", lambda *a, **k: pytest.fail("must not rsync"))
    ok, msg = w.push("kaggle")
    assert ok and "nothing" in msg


def test_contents_lists_nested_files_relative_to_the_mirror(tmp_path, monkeypatch):
    from kdev import persistence as w

    monkeypatch.setattr(w, "mirror_dir", lambda: tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "src" / "b.py").write_text("y")
    assert w.contents() == ["a.txt", "src/b.py"]


def test_push_waits_for_ssh_before_syncing(monkeypatch, tmp_path):
    """The tunnel reports ready before sshd accepts; pushing early silently no-ops."""
    from kdev import persistence as w

    (tmp_path / "a.txt").write_text("x")
    monkeypatch.setattr(w, "mirror_dir", lambda: tmp_path)
    monkeypatch.setattr(w, "wait_reachable", lambda alias, timeout=180: False)
    monkeypatch.setattr(w, "_rsync", lambda *a, **k: pytest.fail("must not rsync"))

    ok, err = w.push("kaggle")
    assert not ok and "reachable" in err


def test_push_retries_a_transient_rsync_failure(monkeypatch, tmp_path):
    from kdev import persistence as w

    (tmp_path / "a.txt").write_text("x")
    monkeypatch.setattr(w, "mirror_dir", lambda: tmp_path)
    monkeypatch.setattr(w, "wait_reachable", lambda alias, timeout=180: True)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        rc = 0 if calls["n"] == 2 else 255
        return type("R", (), {"returncode": rc, "stderr": "broken pipe"})()

    monkeypatch.setattr(w, "_rsync", flaky)
    ok, _ = w.push("kaggle")
    assert ok and calls["n"] == 2


def _versions(monkeypatch, table):
    """table: label -> (files dict or None, state dict or None)."""
    from kdev import persistence as w

    monkeypatch.setattr(
        w, "version_files", lambda c, nb, label: (table.get(label) or (None, None))[0]
    )
    monkeypatch.setattr(
        w, "_state", lambda files: next((st for f, st in table.values() if f is files), None)
    )
    return w


def test_a_complete_saved_version_is_used_on_its_own(monkeypatch):
    w = _versions(monkeypatch, {"v5": ({"a.py": "u"}, {"restored": True, "layers": ["v4"]})})
    assert w.plan_layers(api.Creds("u"), "a/b", 5) == ["v5"]


def test_a_version_kdev_never_touched_counts_as_complete(monkeypatch):
    """A Quick Save from the Kaggle editor, or a pre-kdev run: files, no state."""
    w = _versions(monkeypatch, {"v3": ({"notes.md": "u"}, None)})
    assert w.plan_layers(api.Creds("u"), "a/b", 3) == ["v3"]


def test_empty_versions_are_skipped_not_restored(monkeypatch):
    """A source-only save (group init, attach) has no output. Taking it as the
    workspace would restore nothing and look like everything was lost."""
    w = _versions(
        monkeypatch,
        {"v7": ({}, None), "v6": (None, None), "v5": ({"a.py": "u"}, {"restored": True})},
    )
    assert w.plan_layers(api.Creds("u"), "a/b", 7) == ["v5"]


def test_a_session_cut_short_before_restoring_is_stacked_not_skipped(monkeypatch):
    """Killed mid-restore, or worked in before anyone restored it: whatever it
    holds sits on top of the layers it was meant to have. Skipping it would
    lose anything done in it; using it alone would lose everything before."""
    w = _versions(monkeypatch, {"v9": ({"new.py": "u"}, {"restored": False, "layers": ["v8"]})})
    assert w.plan_layers(api.Creds("u"), "a/b", 9) == ["v8", "v9"]


def test_a_restore_that_missed_files_is_stacked_too(monkeypatch):
    w = _versions(
        monkeypatch,
        {"v9": ({"a": "u"}, {"restored": True, "layers": ["v8"], "missing": ["big.bin"]})},
    )
    assert w.plan_layers(api.Creds("u"), "a/b", 9) == ["v8", "v9"]


def test_stacked_layers_are_capped(monkeypatch):
    w = _versions(
        monkeypatch,
        {"v9": ({"a": "u"}, {"restored": False, "layers": ["v4", "v5", "v6", "v7", "v8"]})},
    )
    assert w.plan_layers(api.Creds("u"), "a/b", 9) == ["v6", "v7", "v8", "v9"]


def test_a_brand_new_notebook_has_nothing_to_restore(monkeypatch):
    w = _versions(monkeypatch, {})
    assert w.plan_layers(api.Creds("u"), "a/b", 0) == []
    assert w.plan_layers(api.Creds("u"), "a/b", 3) == []
