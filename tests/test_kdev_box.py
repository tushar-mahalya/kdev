"""The on-box restore, run locally against temp directories.

Every rule here is one a real session depends on: files restored from any
account's saved version, newest layer winning, this session's own work never
overwritten, and the metadata Kaggle drops (symlinks, empty dirs, exec bits)
put back.
"""

import json
import os
import shutil

import pytest

from kdev import kdev_box as box


@pytest.fixture
def env(tmp_path, monkeypatch):
    work, run, ssh = tmp_path / "working", tmp_path / "run", tmp_path / "ssh"
    work.mkdir()
    monkeypatch.setenv("KDEV_WORK", str(work))
    monkeypatch.setenv("KDEV_RUN", str(run))
    monkeypatch.setenv("KDEV_SSH_DIR", str(ssh))
    return work


def _store(blobs):
    """A fake Kaggle: url -> bytes, with a fetch and a fetch_bytes."""

    def fetch(url, dest):
        if url not in blobs:
            raise OSError("404")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blobs[url])

    def fetch_bytes(url):
        if url not in blobs:
            raise OSError("404")
        return blobs[url]

    return fetch, fetch_bytes


def _layer(label, files):
    return {"label": label, "files": {n: f"{label}/{n}" for n in files}}


def test_startup_confirmation_is_bound_to_the_elected_session(env):
    box.init_state(["v1"], session="42", run_by="alice")
    before = json.loads((env / box.STATE).read_text())
    assert before["startup_confirmed"] is False
    for sid in ("43", "", "invalid", "-42"):
        assert box.main(["--confirm-start", sid]) == 3
        assert json.loads((env / box.STATE).read_text()) == before
    assert box.main(["--confirm-start", "42"]) == 0
    assert json.loads((env / box.STATE).read_text()) == {**before, "startup_confirmed": True}
    assert box.main(["--confirm-start", "42"]) == 0


def test_restore_receiver_checks_identity_before_writing_anything(env, monkeypatch):
    import io

    box.init_state([], session="42")
    monkeypatch.setattr(
        box.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch restore")
    )
    payload = {"script": "should never be written", "plan": {"layers": []}, "session": "43"}
    with pytest.raises(ValueError, match="different session"):
        box.receive(io.StringIO(json.dumps(payload)))
    assert not box.run_dir().exists()


def test_a_saved_version_comes_back_whole(env):
    fetch, fb = _store({"v1/a.py": b"a", "v1/src/b.py": b"b", "v1/.git/HEAD": b"ref"})
    state = box.restore({"layers": [_layer("v1", ["a.py", "src/b.py", ".git/HEAD"])]}, fetch, fb)
    assert state["restored"] and state["done"] == 3 and not state["missing"]
    assert (env / "src/b.py").read_bytes() == b"b"
    assert (env / ".git/HEAD").read_bytes() == b"ref"


def test_the_newest_layer_wins(env):
    fetch, fb = _store({"v1/a.py": b"old", "v2/a.py": b"new", "v1/only-old.py": b"x"})
    box.restore(
        {"layers": [_layer("v1", ["a.py", "only-old.py"]), _layer("v2", ["a.py"])]}, fetch, fb
    )
    assert (env / "a.py").read_bytes() == b"new"
    assert (env / "only-old.py").exists()


def test_work_done_in_this_session_is_never_overwritten(env):
    """The late-restore case: you connected, worked, then ran `kdev restore`."""
    (env / "a.py").write_bytes(b"written this session")
    fetch, fb = _store({"v1/a.py": b"saved last week", "v1/b.py": b"b"})
    box.restore({"layers": [_layer("v1", ["a.py", "b.py"])]}, fetch, fb)
    assert (env / "a.py").read_bytes() == b"written this session"
    assert (env / "b.py").exists()


def test_the_live_sessions_runtime_files_are_not_restored_over(env):
    (env / "cloudflared.log").write_text("live")
    fetch, fb = _store(
        {
            "v1/cloudflared.log": b"dead",
            "v1/.kdev/state.json": b"{}",
            "v1/__notebook__.ipynb": b"{}",
        }
    )
    box.restore(
        {"layers": [_layer("v1", ["cloudflared.log", ".kdev/state.json", "__notebook__.ipynb"])]},
        fetch,
        fb,
    )
    assert (env / "cloudflared.log").read_text() == "live"
    assert not (env / "__notebook__.ipynb").exists()
    assert json.loads((env / ".kdev/state.json").read_text())["restored"] is True


@pytest.mark.parametrize("hostile", ["/etc/passwd", "../escape.txt", "a/../../b"])
def test_a_name_that_leaves_the_workspace_is_refused(env, hostile):
    fetch, fb = _store({f"v1/{hostile}": b"x"})
    box.restore({"layers": [_layer("v1", [hostile])]}, fetch, fb)
    assert not (env.parent / "escape.txt").exists()
    assert [p for p in env.rglob("*") if p.is_file() and ".kdev" not in p.parts] == []


def test_a_file_that_will_not_download_is_named_not_hidden(env):
    fetch, fb = _store({"v1/ok.py": b"ok"})
    box.ATTEMPTS, saved = 1, box.ATTEMPTS
    try:
        state = box.restore({"layers": [_layer("v1", ["ok.py", "gone.py"])]}, fetch, fb)
    finally:
        box.ATTEMPTS = saved
    assert state["restored"] and state["missing"] == ["gone.py"]


def test_symlinks_empty_dirs_and_exec_bits_survive_the_round_trip(env):
    """A venv is made of symlinks and executables; Kaggle's snapshot keeps
    neither, so without this it comes back broken."""
    (env / "bin").mkdir()
    (env / "bin/tool").write_text("#!/bin/sh\n")
    (env / "bin/tool").chmod(0o755)
    (env / "bin/alias").symlink_to("tool")
    (env / "lib").mkdir()
    (env / "lib64").symlink_to("lib")
    (env / "empty").mkdir()
    meta = box.scan_meta(env)
    assert meta["symlinks"] == {"bin/alias": "tool", "lib64": "lib"}
    assert "empty" in meta["dirs"] and "bin/tool" in meta["exec"]

    # What Kaggle keeps: regular files only, modes lost.
    blobs = {"v1/bin/tool": b"#!/bin/sh\n", "v1/.kdev/meta.json": json.dumps(meta).encode()}
    for p in list(env.iterdir()):
        if p.is_symlink() or p.is_file():
            p.unlink()
        else:
            shutil.rmtree(p)
    fetch, fb = _store(blobs)
    box.restore({"layers": [_layer("v1", ["bin/tool", ".kdev/meta.json"])]}, fetch, fb)

    assert os.readlink(env / "bin/alias") == "tool"
    assert os.readlink(env / "lib64") == "lib"
    assert (env / "empty").is_dir()
    assert os.stat(env / "bin/tool").st_mode & 0o111


def test_a_later_regular_file_beats_an_earlier_symlink(env):
    old_meta = {"symlinks": {"cfg": "cfg.real"}, "dirs": [], "exec": []}
    fetch, fb = _store(
        {
            "v1/.kdev/meta.json": json.dumps(old_meta).encode(),
            "v2/cfg": b"now a real file",
            "v2/.kdev/meta.json": b"{}",
        }
    )
    box.restore(
        {"layers": [_layer("v1", [".kdev/meta.json"]), _layer("v2", ["cfg", ".kdev/meta.json"])]},
        fetch,
        fb,
    )
    assert not (env / "cfg").is_symlink()
    assert (env / "cfg").read_bytes() == b"now a real file"


def test_every_machine_that_ever_started_the_box_stays_authorised(env, tmp_path):
    box.install_keys("ssh-ed25519 AAAA machine-b")
    fetch, fb = _store({"v1/.kdev/authorized_keys": b"ssh-ed25519 AAAA machine-a\n"})
    box.restore({"layers": [_layer("v1", [".kdev/authorized_keys"])]}, fetch, fb)
    live = (tmp_path / "ssh/authorized_keys").read_text()
    assert "machine-a" in live and "machine-b" in live
    assert oct((tmp_path / "ssh/authorized_keys").stat().st_mode & 0o777) == "0o600"


def test_state_says_not_restored_until_it_is(env):
    """What the next session reads to decide whether to stack layers."""
    box.init_state(["v3"], session="42", run_by="alice")
    s = json.loads((env / ".kdev/state.json").read_text())
    assert s["restored"] is False and s["layers"] == ["v3"] and s["session"] == "42"
    box.init_state([])
    assert json.loads((env / ".kdev/state.json").read_text())["restored"] is True


def test_metadata_is_not_recorded_mid_restore(env, tmp_path):
    """While a restore runs, the tree is half-built; recording it would save a
    half-set of links. The "running restore" here is a real process whose
    command line names kdev_box.py, so Linux checks it through /proc (as on the
    Kaggle box) and macOS by pid -- a stand-in pid would only pass on one."""
    import subprocess
    import sys

    (tmp_path / "run").mkdir()
    restore = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "/root/.kdev/kdev_box.py"]
    )
    try:
        (tmp_path / "run/restore.pid").write_text(str(restore.pid))
        assert box.record_meta() is False
    finally:
        restore.kill()
        restore.wait()
    (tmp_path / "run/restore.pid").unlink()
    assert box.record_meta() is True
    assert (env / ".kdev/meta.json").exists()


def test_a_session_started_empty_on_purpose_keeps_the_history_it_skipped(env):
    """`kdev up --no-restore` still plans layers, so the box records what it
    should hold. Recording `restored: true` with no layers would make the next
    `kdev up` build on this near-empty version alone and drop everything before."""
    box.init_state(["v10"])  # what `up --no-restore` now does
    s = json.loads((env / ".kdev/state.json").read_text())
    assert s["restored"] is False and s["layers"] == ["v10"]


def test_restore_records_this_sessions_own_links_too(env):
    """The end-of-restore metadata is scanned from disk: a link this session
    made before the restore ran must still be in it."""
    (env / "target.txt").write_text("t")
    (env / "mine").symlink_to("target.txt")  # made this session, before restore
    fetch, fb = _store({"v1/a.py": b"a"})
    box.restore({"layers": [_layer("v1", ["a.py"])]}, fetch, fb)
    meta = json.loads((env / ".kdev/meta.json").read_text())
    assert meta["symlinks"].get("mine") == "target.txt"


def test_a_clashing_path_is_reported_not_fatal(env, tmp_path):
    """An old version's empty dir `logs` vs a file `logs` made this session:
    the restore must still finish, and still merge the machines' keys."""
    (env / "logs").write_text("a file now")
    meta = {"dirs": ["logs"], "symlinks": {}, "exec": []}
    fetch, fb = _store(
        {
            "v1/.kdev/meta.json": json.dumps(meta).encode(),
            "v1/.kdev/authorized_keys": b"ssh-ed25519 AAAA machine-a\n",
        }
    )
    state = box.restore(
        {"layers": [_layer("v1", [".kdev/meta.json", ".kdev/authorized_keys"])]}, fetch, fb
    )
    assert state["restored"] and state["unapplied"] == ["logs"]
    assert (env / "logs").read_text() == "a file now"
    assert "machine-a" in (tmp_path / "ssh/authorized_keys").read_text()


def test_a_reused_pid_is_not_mistaken_for_a_running_restore(env, tmp_path, monkeypatch):
    """A restore killed hard leaves its pid file; if another process later gets
    that pid, metadata recording must not stop for the rest of the session."""
    (tmp_path / "run").mkdir()
    (tmp_path / "run/restore.pid").write_text("4242")
    proc = tmp_path / "proc/4242"
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"/usr/bin/git\x00push\x00")
    real = box.Path

    def fake_path(*a):
        p = real(*a)
        return tmp_path / "proc/4242/cmdline" if str(p) == "/proc/4242/cmdline" else p

    monkeypatch.setattr(box, "Path", fake_path)
    assert box._restore_running() is False
    (proc / "cmdline").write_bytes(b"python3\x00/root/.kdev/kdev_box.py\x00plan.json\x00")
    assert box._restore_running() is True


def test_only_the_boxs_own_runtime_files_are_skipped(env):
    """Measured live: a project's web/static/custom.css never came back, and
    was not even reported missing, because the skip matched any custom.css."""
    names = ["custom.css", "cloudflared.log", "web/static/custom.css", "logs/cloudflared.log"]
    fetch, fb = _store({f"v1/{n}": n.encode() for n in names})
    box.restore({"layers": [_layer("v1", names)]}, fetch, fb)
    assert not (env / "custom.css").exists() and not (env / "cloudflared.log").exists()
    assert (env / "web/static/custom.css").read_bytes() == b"web/static/custom.css"
    assert (env / "logs/cloudflared.log").exists()


def test_signed_urls_with_raw_names_are_escaped():
    """Kaggle signs `spaced name ü.txt` into the URL unescaped; urllib refuses
    that, so the file could never be restored."""
    raw = "https://www.kaggleusercontent.com/kf/1/tok/data/spaced name ü.txt?X-Sig=a%2Fb"
    assert box._url(raw) == (
        "https://www.kaggleusercontent.com/kf/1/tok/data/spaced%20name%20%C3%BC.txt?X-Sig=a%2Fb"
    )
    assert box._url(box._url(raw)) == box._url(raw)


def test_a_killed_restores_leftover_part_file_never_lands_on_the_real_one(env):
    """Measured live: a restore killed mid-download saved big.bin.kdev-part;
    the next restore fetched it as a user file onto big.bin's own temp path,
    and big.bin came back as the 60 MB partial instead of 1 GB."""
    fetch, fb = _store({"v1/big.bin": b"whole", "v2/big.bin.kdev-part": b"partial"})
    state = box.restore(
        {"layers": [_layer("v1", ["big.bin"]), _layer("v2", ["big.bin.kdev-part"])]}, fetch, fb
    )
    assert (env / "big.bin").read_bytes() == b"whole"
    assert not (env / "big.bin.kdev-part").exists() and state["total"] == 1


def test_exec_bits_are_recorded_before_any_file_arrives(env):
    """A restore killed part-way must still save the bits Kaggle drops, or the
    next restore builds on its copies of .git/hooks without them."""
    meta = json.dumps({"exec": ["hook"], "symlinks": {"ln": "hook"}, "dirs": ["empty"]}).encode()
    seen = []

    def fetch(url, dest):
        seen.append(json.loads((env / box.META).read_text()))
        dest.write_bytes(b"#!/bin/sh\n")

    box.restore({"layers": [_layer("v1", ["hook", box.META])]}, fetch, lambda url: meta)
    assert seen[0]["exec"] == ["hook"] and seen[0]["symlinks"] == {"ln": "hook"}
    assert os.access(env / "hook", os.X_OK)


def test_a_download_cut_short_is_not_kept_as_whole(tmp_path):
    """http.client ends a body cut off mid-stream quietly; without the length
    check a truncated file would be renamed into place."""
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nonly ten b")
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    dest = tmp_path / "f.bin"
    with pytest.raises(OSError, match="short"):
        box._fetch(f"http://127.0.0.1:{srv.getsockname()[1]}/f", dest)
    assert not dest.exists() and not list(tmp_path.iterdir())


def test_a_restore_logs_each_file_as_it_lands(env):
    """What the next plan uses to tell a file deleted since from one never restored."""
    fetch, fb = _store({"v1/a.py": b"a", "v1/b c ü.txt": b"b"})
    box.restore({"layers": [_layer("v1", ["a.py", "b c ü.txt"])]}, fetch, fb)
    lines = (env / box.FETCHED).read_text().splitlines()
    assert sorted(json.loads(x) for x in lines) == ["a.py", "b c ü.txt"]


def test_filling_in_from_another_version_keeps_the_base(env):
    """`kdev restore --from v17` on a box built from v18 recorded v17 as its
    base, so the next plain `kdev restore` filled in from the wrong version."""
    fetch, fb = _store({"v18/a.py": b"a", "v17/old.py": b"o"})
    box.restore({"layers": [_layer("v18", ["a.py"])]}, fetch, fb)
    state = box.restore({"layers": [_layer("v17", ["old.py"])]}, fetch, fb)
    assert state["layers"] == ["v18"] and (env / "old.py").exists()
