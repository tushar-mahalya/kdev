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
    ):
        assert "/" + noise in w.EXCLUDES
    assert "__pycache__" in w.EXCLUDES


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


def test_session_history_caps_scanned_versions_independently_of_limit(monkeypatch):
    from kdev import persistence as w

    scanned = []
    monkeypatch.setattr(w, "version_files", lambda c, nb, label: scanned.append(label) or None)

    assert w.session_history(api.Creds("u"), "a/b", latest=100, limit=2) == []
    assert scanned == [f"v{number}" for number in range(100, 100 - w.WALK_BACK, -1)]


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


def test_unconfirmed_race_losers_cannot_replace_the_saved_winner(monkeypatch):
    from kdev import persistence as w

    table = {
        f"v{n}": ({w.STATE: f"state{n}"}, {"startup_confirmed": False, "restored": True})
        for n in range(2, 25)
    }
    table["v1"] = (
        {"important.py": "url", w.STATE: "state1"},
        {"startup_confirmed": True, "restored": True},
    )
    w = _versions(monkeypatch, table)
    assert w.plan_layers(api.Creds("u"), "a/b", 24) == ["v1"]
    # Explicit recovery still lists every loser's output.
    assert w.version_files(api.Creds("u"), "a/b", "v24") == table["v24"][0]


def test_confirmed_empty_workspace_does_not_resurrect_deleted_files(monkeypatch):
    from kdev import persistence as w

    w = _versions(
        monkeypatch,
        {
            "v2": ({w.STATE: "state2"}, {"startup_confirmed": True, "restored": True}),
            "v1": ({"deleted.py": "url"}, None),
        },
    )
    assert w.plan_layers(api.Creds("u"), "a/b", 2) == ["v2"]


def test_confirmed_no_restore_session_keeps_its_history_layers(monkeypatch):
    from kdev import persistence as w

    w = _versions(
        monkeypatch,
        {
            "v2": (
                {w.STATE: "state2"},
                {"startup_confirmed": True, "restored": False, "layers": ["v1"]},
            )
        },
    )
    assert w.plan_layers(api.Creds("u"), "a/b", 2) == ["v1", "v2"]


def test_unreadable_saved_state_fails_before_workspace_history_is_guessed(monkeypatch):
    from kdev import persistence as w
    from kdev.errors import KdevError

    w = _versions(monkeypatch, {"v2": ({w.STATE: "bad-url"}, None)})
    with pytest.raises(KdevError, match="Could not read the saved state"):
        w.plan_layers(api.Creds("u"), "a/b", 2)


@pytest.mark.parametrize("status", [0, 401, 403, 429, 500])
def test_failed_output_lookup_is_not_treated_as_empty_history(monkeypatch, status):
    from kdev import persistence as w

    def failed(*a):
        raise api.KaggleError("output lookup failed", status)

    monkeypatch.setattr(api, "session_output", failed)
    with pytest.raises(api.KaggleError, match="output lookup failed"):
        w.newest_saved(api.Creds("u"), "a/b", 3)


def test_absent_output_version_can_still_be_skipped(monkeypatch):
    from kdev import persistence as w

    def absent(*a):
        raise api.KaggleError("no output", 404)

    monkeypatch.setattr(api, "session_output", absent)
    assert w.newest_saved(api.Creds("u"), "a/b", 3) == ("", {})


def test_unconfirmed_history_limit_fails_closed_instead_of_restoring_empty(monkeypatch):
    from kdev import persistence as w
    from kdev.errors import KdevError

    table = {
        f"v{n}": ({w.STATE: f"state{n}"}, {"startup_confirmed": False})
        for n in range(1, w.MAX_UNCONFIRMED + 2)
    }
    w = _versions(monkeypatch, table)
    with pytest.raises(KdevError, match="Too many unconfirmed"):
        w.plan_layers(api.Creds("u"), "a/b", w.MAX_UNCONFIRMED + 1)


@pytest.mark.parametrize("sid", ["", "not-a-number", "0", "-1"])
def test_invalid_session_id_cannot_confirm_startup(monkeypatch, sid):
    from kdev import persistence as w

    monkeypatch.setattr(w, "_ssh", lambda *a, **kw: pytest.fail("invalid id"))
    assert not w.confirm_start("box", sid)


@pytest.mark.parametrize(
    "state,expected",
    [
        ({"session": "42"}, True),
        ({"session": "43"}, False),
        ({"session": "42", "startup_confirmed": False}, False),
    ],
)
def test_legacy_startup_confirmation_still_requires_matching_session(monkeypatch, state, expected):
    import json
    import subprocess

    from kdev import persistence as w

    def ssh(alias, command, **kw):
        return (
            subprocess.CompletedProcess([], 0, json.dumps(state), "")
            if command.startswith("cat ")
            else subprocess.CompletedProcess([], 2, "", "")
        )

    monkeypatch.setattr(w, "_ssh", ssh)
    assert w.confirm_start("box", "42") is expected


def test_startup_confirmation_retries_wrong_backend_until_correct_session(monkeypatch):
    import subprocess
    import time

    from kdev import persistence as w

    elapsed = [0.0]
    calls = []
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay))

    def ssh(alias, command, **kw):
        calls.append((command, kw["timeout"]))
        return subprocess.CompletedProcess([], 3 if len(calls) == 1 else 0, "", "")

    monkeypatch.setattr(w, "_ssh", ssh)
    assert w.confirm_start("box", "42", timeout=3)
    assert calls == [
        ("/root/.kdev/run --confirm-start 42", 3),
        ("/root/.kdev/run --confirm-start 42", 1),
    ]


def test_wrong_backend_timeout_cannot_confirm_startup(monkeypatch):
    import subprocess
    import time

    from kdev import persistence as w

    elapsed = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay))
    monkeypatch.setattr(w, "_ssh", lambda *a, **kw: subprocess.CompletedProcess([], 3, "", ""))
    assert not w.confirm_start("box", "42", timeout=3)
    assert elapsed[0] == 3


def test_background_ssh_never_asks_for_a_tty(monkeypatch):
    """The kdev ssh block says RequestTTY yes, so without -T every background
    probe took over the terminal (raw mode) and smeared the live board."""
    import subprocess

    from kdev import persistence, session

    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    persistence.wait_reachable("kaggle")
    persistence.box_state("kaggle")
    session.reachable("kaggle")
    assert len(calls) == 3
    assert all(argv[:2] == ["ssh", "-T"] for argv in calls), calls


def test_up_waits_for_a_cancelled_session_to_finish_saving(monkeypatch):
    """Measured live: after a cancel the version listed 0, then 29, then 54
    files while CANCEL_REQUESTED. Planning in that window drops the session."""
    from kdev import session

    seen = iter(["CANCEL_REQUESTED", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED"])
    calls = []

    def status(creds, slug, version=""):
        assert version == ""
        calls.append(1)
        return {"status": next(seen)}

    monkeypatch.setattr(api, "session_status", status)
    monkeypatch.setattr(session.time, "sleep", lambda s: None)
    session.wait_saved(api.Creds("u"), "a/b")
    assert len(calls) == 3


@pytest.mark.skipif(not __import__("shutil").which("rsync"), reason="needs rsync")
def test_backup_mirrors_the_box_and_pushing_it_back_never_overwrites(monkeypatch, tmp_path):
    """The backup kept files deleted on the box (and a whole other notebook's),
    and `restore --from-backup` then overwrote what the session had written."""
    from kdev import persistence as w

    box, mirror = tmp_path / "box", tmp_path / "mirror"
    box.mkdir()
    mirror.mkdir()
    (mirror / "stale.txt").write_text("from an old notebook")
    (box / "work.txt").write_text("v1")
    monkeypatch.setattr(w, "REMOTE", str(box))
    monkeypatch.setattr(w, "mirror_dir", lambda: mirror)
    monkeypatch.setattr(w, "wait_reachable", lambda alias, timeout=180: True)
    # rsync treats "alias:path" as remote; an empty alias leaves a local path.
    monkeypatch.setattr(w, "_rsync", lambda src, dst, *x, **k: _local(src, dst, *x))

    assert w.pull("")[0]
    assert sorted(p.name for p in mirror.iterdir()) == ["work.txt"]

    (box / "work.txt").write_text("v2, written this session")
    (box / "gone.txt").unlink(missing_ok=True)
    (mirror / "only-in-backup.txt").write_text("b")
    assert w.push("")[0]
    assert (box / "work.txt").read_text() == "v2, written this session"
    assert (box / "only-in-backup.txt").exists()


def _local(src, dst, *extra):
    import subprocess

    from kdev import persistence as w

    cmd = ["rsync", "-a", *extra] + [f"--exclude={p}" for p in w.EXCLUDES]
    return subprocess.run([*cmd, src.lstrip(":"), dst.lstrip(":")], capture_output=True, text=True)


def test_stacked_layers_only_fill_in_what_the_newest_never_got(monkeypatch):
    """Measured live: a file deleted in a session came back because the layer
    under it was restored whole. The layer under only supplies what the newest
    session's restore missed or never reached."""
    base = {
        "kept.py": "b/kept",
        "deleted.py": "b/deleted",
        "failed.bin": "b/failed",
        "never.py": "b/never",
        ".kdev/meta.json": "b/meta",
    }
    missed = {"kept.py": "t/kept", ".kdev/state.json": "t/state"}
    w = _versions(
        monkeypatch,
        {
            "v1": (base, {"restored": True}),
            "v2": (missed, {"restored": True, "layers": ["v1"], "missing": ["failed.bin"]}),
        },
    )
    plan = w.build_plan(api.Creds("u"), "a/b", ["v1", "v2"])
    assert sorted(plan["layers"][0]["files"]) == [".kdev/meta.json", "failed.bin"]

    killed = {"kept.py": "t/kept", ".kdev/state.json": "t/state", ".kdev/fetched": "t/f"}
    w = _versions(
        monkeypatch,
        {
            "v1": (base, {"restored": True}),
            "v2": (killed, {"restored": False, "layers": ["v1"]}),
        },
    )
    monkeypatch.setattr(w, "_fetched", lambda files: {"kept.py", "deleted.py"})
    plan = w.build_plan(api.Creds("u"), "a/b", ["v1", "v2"])
    assert sorted(plan["layers"][0]["files"]) == [".kdev/meta.json", "failed.bin", "never.py"]
