"""Every command, end to end through `kdev.cli.main`, against a fake Kaggle.

These run the real parser, the real command code and the real error
rendering; only the network, ssh and the browser are replaced. Each test says
what a user would type and what they must see.
"""

import json
import sys

import pytest

from kdev import api, auth, cli, config, persistence, session, sshcfg


class FakeKaggle:
    """Just enough of Kaggle for the commands to run against."""

    def __init__(self):
        self.status = "COMPLETE"
        self.statuses: dict[str, str] = {}
        self.states: dict[str, dict] = {}
        self.version = 3
        self.saved: list[dict] = []
        self.cancelled: list[tuple[str, int]] = []
        self.logs: list[str] = []
        self.source = ""
        self.owner_of_session = "alice"
        self.outputs = {
            "": [{"name": "notes.md", "url": "u"}],
            "v3": [{"name": "notes.md", "url": "u"}],
        }

    def install(self, monkeypatch):
        mp = monkeypatch.setattr
        mp(
            api,
            "session_status",
            lambda c, s, v="": {"status": self.statuses.get(v, self.status)},
        )
        mp(persistence, "_state", lambda files: self.states.get(files.get(persistence.STATE, "")))
        mp(
            api,
            "get_kernel",
            lambda c, s: {
                "id": 42,
                "currentVersionNumber": self.version,
                "machineShape": "NvidiaTeslaT4",
            },
        )
        mp(api, "kernel_id", lambda c, s: 42)
        mp(api, "kernel_source", lambda c, s: self.source)
        mp(api, "session_output", lambda c, s, v="": list(self.outputs.get(v, [])))
        mp(
            api,
            "quota",
            lambda c: {
                "gpuQuota": {"totalTimeAllowed": "108000s", "timeUsed": "3600s"},
                "tpuQuota": {"totalTimeAllowed": "72000s"},
            },
        )
        mp(
            api,
            "save_kernel",
            lambda c, **kw: (
                self.saved.append(kw) or {"versionNumber": self.version + 1, "kernelId": 42}
            ),
        )
        mp(api, "stream_logs", lambda c, s, wait_seconds=300, idle=None: iter(self.logs))
        mp(
            api,
            "get_policy",
            lambda c, kid: {
                "bindings": [
                    {
                        "role": api.ROLE_EDITOR,
                        "members": [
                            {
                                "group": {
                                    "avatar": {"slug": "crew", "name": "Crew", "memberCount": 3}
                                }
                            }
                        ],
                    }
                ]
            },
        )
        mp(api, "group_members", lambda c, g: [{"username": "alice"}, {"username": "bob"}])
        mp(api, "list_shared_kernels", lambda c: [{"ref": "alice/box", "title": "box"}])
        mp(api, "share_with_group", lambda c, kid, g: {})

        def cancel(c, sid):
            if c.username != self.owner_of_session:
                raise api.KaggleError("Permission 'kernelSessions.cancel' was denied", 403)
            self.cancelled.append((c.username, sid))
            return {}

        mp(api, "cancel_session", cancel)
        return self


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated kdev home: its own config dir, ssh config, no network."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "kdev")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "kdev" / "config.json")
    monkeypatch.setattr(sshcfg, "SSH_CONFIG", tmp_path / "ssh" / "config")
    monkeypatch.setattr(auth, "signed_in", lambda name: True)
    monkeypatch.setattr(auth, "access_token", lambda name: f"token-of-{name}")
    monkeypatch.setattr(session, "reachable", lambda alias, timeout=10: False)
    monkeypatch.setattr(persistence, "wait_reachable", lambda alias, timeout=180: True)
    monkeypatch.setattr(persistence, "box_state", lambda alias: {})
    return tmp_path


@pytest.fixture
def kaggle(monkeypatch):
    return FakeKaggle().install(monkeypatch)


def configured(**extra) -> config.Config:
    cfg = config.Config(
        notebook="alice/box",
        notebook_id=42,
        group_slug="crew",
        tunnel_hostname="box.example.com",
        tunnel_id="t",
        tunnel_credentials='{"TunnelSecret": "s3cret"}',  # pragma: allowlist secret
        ssh_public_key="ssh-ed25519 AAAA me@laptop",
        default_gpu="t4",
        default_hours=9.0,
        **extra,
    )
    cfg.add("alice", config.Profile("alice"))
    cfg.add("bob", config.Profile("bob"))
    config.save(cfg)
    return cfg


def kdev(capsys, *argv) -> tuple[int, str, str]:
    """Run `kdev <argv>` exactly as a user would; return (exit, stdout, stderr)."""
    old = sys.argv
    sys.argv = ["kdev", *argv]
    try:
        cli.main()
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = old
    out, err = capsys.readouterr()
    return code, out, err


# --- basics -------------------------------------------------------------------


def test_version(home, capsys):
    code, out, _ = kdev(capsys, "--version")
    assert code == 0 and out.startswith("kdev ")


def test_an_unknown_command_is_a_usage_error(home, capsys):
    code, _, err = kdev(capsys, "nosuch")
    assert code == 2 and "No such command" in err


def test_nothing_configured_says_how_to_start(home, capsys):
    code, _, err = kdev(capsys)
    assert code == 1 and "kdev setup" in err


def test_the_overview_shows_the_box_and_every_account(home, kaggle, capsys):
    configured()
    code, out, _ = kdev(capsys)
    assert code == 0
    assert "stopped" in out and "alice" in out and "bob" in out
    assert "kdev up" in out  # the next step, when not in a terminal


# --- history --------------------------------------------------------------------


def test_history_lists_recent_saved_sessions(home, kaggle, capsys):
    configured()
    kaggle.version = 4
    kaggle.outputs = {
        "v4": [],
        "v3": [
            {"name": "notes.md", "url": "u3"},
            {"name": ".kdev/state.json", "url": "state:v3"},
        ],
        "v2": [
            {"name": "src/app.py", "url": "u2"},
            {"name": "data.bin", "url": "d2"},
            {"name": ".kdev/state.json", "url": "state:v2"},
        ],
        "v1": [{"name": "old.txt", "url": "u1"}],
    }
    kaggle.states = {
        "state:v3": {
            "run_by": "alice",
            "started": "2026-09-25T10:00:00Z",
            "ends": 1790334000,
            "restored": True,
            "finished": "2026-09-25T10:01:00Z",
        },
        "state:v2": {
            "run_by": "bob",
            "started": "2026-09-24T12:00:00Z",
            "ends": 1790251200,
            "restored": False,
        },
    }
    kaggle.statuses = {"v3": "COMPLETE", "v2": "ERROR", "v1": "CANCEL_ACKNOWLEDGED"}

    code, out, err = kdev(capsys, "history", "-n", "2")
    assert code == 0, err
    assert "v3" in out and "alice" in out and "COMPLETE" in out
    assert "v2" in out and "bob" in out and "ERROR" in out
    assert "v1" not in out


def test_history_json_is_machine_readable(home, kaggle, capsys):
    configured()
    kaggle.version = 2
    kaggle.outputs = {
        "v2": [
            {"name": "work.py", "url": "u2"},
            {"name": ".kdev/state.json", "url": "state:v2"},
        ],
        "v1": [{"name": "plain.txt", "url": "u1"}],
    }
    kaggle.states = {
        "state:v2": {
            "run_by": "alice",
            "started": "2026-09-25T10:00:00Z",
            "ends": 1790334000,
            "restored": True,
            "finished": "2026-09-25T10:02:00Z",
        }
    }
    kaggle.statuses = {"v2": "COMPLETE", "v1": "CANCEL_ACKNOWLEDGED"}

    code, out, err = kdev(capsys, "history", "--json")
    data = json.loads(out)
    assert code == 0, err
    assert data["notebook"] == "alice/box"
    assert [row["version"] for row in data["sessions"]] == ["v2", "v1"]
    assert data["sessions"][0] == {
        "version": "v2",
        "run_by": "alice",
        "started": "2026-09-25T10:00:00Z",
        "ends": 1790334000,
        "status": "COMPLETE",
        "files": 1,
        "restored": True,
        "restore_finished": "2026-09-25T10:02:00Z",
    }
    assert data["sessions"][1]["status"] == "CANCEL_ACKNOWLEDGED"
    assert data["sessions"][1]["run_by"] is None
    assert data["sessions"][1]["restored"] is None


# --- accounts -----------------------------------------------------------------


def test_account_lists_quota(home, kaggle, capsys):
    configured()
    code, out, _ = kdev(capsys, "account")
    assert code == 0 and "29.0h" in out and "58.0h of GPU left across 2" in out


def test_account_json_is_machine_readable(home, kaggle, capsys):
    configured()
    code, out, _ = kdev(capsys, "account", "--json")
    data = json.loads(out)
    assert code == 0 and [a["account"] for a in data] == ["alice", "bob"]
    assert data[0]["gpu_seconds_left"] == 104400 and data[0]["active"] is True


def test_account_use_switches_and_rejects_strangers(home, kaggle, capsys):
    configured()
    assert kdev(capsys, "account", "use", "bob")[0] == 0
    assert config.load().active == "bob"
    code, _, err = kdev(capsys, "account", "use", "carol")
    assert code == 1 and "No account called 'carol'" in err


def test_account_remove_forgets_it_and_the_last_one_stays(home, kaggle, capsys, monkeypatch):
    configured()
    forgotten = []
    monkeypatch.setattr(auth, "forget", forgotten.append)
    assert kdev(capsys, "account", "remove", "alice", "-y")[0] == 0
    cfg = config.load()
    assert list(cfg.profiles) == ["bob"] and cfg.active == "bob" and forgotten == ["alice"]
    code, _, err = kdev(capsys, "account", "remove", "bob", "-y")
    assert code == 1 and "only account" in err


def test_a_signed_out_account_is_shown_with_the_fix(home, kaggle, capsys, monkeypatch):
    configured()

    def quota(c):
        if c.username == "bob":
            raise api.KaggleError("GetAcceleratorQuotaStatistics: bob is not signed in", 401)
        return {"gpuQuota": {"totalTimeAllowed": "108000s"}}

    monkeypatch.setattr(api, "quota", quota)
    code, out, _ = kdev(capsys, "account")
    assert code == 0 and "kdev account add bob" in out


# --- config -------------------------------------------------------------------


def test_config_shows_settings_but_never_secrets(home, kaggle, capsys):
    configured(tunnel_token="tok-secret", git_deploy_key="-----BEGIN KEY-----")
    code, out, _ = kdev(capsys, "config")
    assert code == 0 and "T4" in out and "9h" in out
    assert "tok-secret" not in out and "BEGIN KEY" not in out and "set (hidden)" in out


def test_config_set_validates_and_unset_restores(home, kaggle, capsys):
    configured()
    assert kdev(capsys, "config", "set", "gpu", "p100")[0] == 0
    assert config.load().default_gpu == "p100"
    code, _, err = kdev(capsys, "config", "set", "hours", "20")
    assert code == 1 and "at most 12" in err
    code, _, err = kdev(capsys, "config", "set", "ssh-alias", "has space")
    assert code == 1
    assert kdev(capsys, "config", "unset", "gpu")[0] == 0
    assert config.load().default_gpu == ""


def test_config_set_gpu_tpu_lowers_hours_above_cap(home, kaggle, capsys):
    cfg = configured()
    cfg.default_hours = 12.0
    config.save(cfg)
    code, out, _ = kdev(capsys, "config", "set", "gpu", "tpu")
    assert code == 0
    assert "hours lowered to 9h, TPU's limit" in out
    cfg = config.load()
    assert cfg.default_gpu == "tpu"
    assert cfg.default_hours == 9.0


def test_config_set_reads_keys_from_files_not_the_command_line(home, kaggle, capsys, tmp_path):
    configured()
    key = tmp_path / "id.pub"
    key.write_text("ssh-ed25519 AAAAnew other@machine\n")
    assert kdev(capsys, "config", "set", "ssh-key", str(key))[0] == 0
    assert config.load().ssh_public_key == "ssh-ed25519 AAAAnew other@machine"
    bad = tmp_path / "not.pub"
    bad.write_text("hello")
    code, _, err = kdev(capsys, "config", "set", "ssh-key", str(bad))
    assert code == 1 and "public key" in err


# --- workspace ----------------------------------------------------------------


def test_workspace_describes_sharing_members_and_saves(home, kaggle, capsys):
    configured()
    code, out, _ = kdev(capsys, "workspace")
    assert code == 0 and "crew can edit" in out and "alice, bob" in out and "v3" in out


def test_workspace_files_hides_kdevs_own_files(home, kaggle, capsys):
    configured()
    kaggle.outputs["v3"] = [
        {"name": n, "url": "u"}
        for n in ("notes.md", ".kdev/state.json", "cloudflared.log", "src/app.py", "x.kdev-part")
    ]
    code, out, _ = kdev(capsys, "workspace", "files", "--json")
    assert code == 0 and json.loads(out)["files"] == ["notes.md", "src/app.py"]


def test_workspace_files_shows_the_saved_files_while_a_box_runs(home, kaggle, capsys):
    """The running box's own version has no files until it ends; showing that
    one said "nothing saved yet" about a workspace that was all there."""
    configured()
    kaggle.version = 4
    kaggle.outputs["v3"] = [{"name": "notes.md", "url": "u"}]
    code, out, _ = kdev(capsys, "workspace", "files", "--json")
    got = json.loads(out)
    assert code == 0 and (got["version"], got["files"]) == ("v3", ["notes.md"])


def test_workspace_use_takes_the_tunnel_from_the_notebook(home, kaggle, capsys):
    from kdev import bootstrap

    cfg = config.Config(ssh_public_key="k")
    cfg.add("alice", config.Profile("alice"))
    config.save(cfg)
    kaggle.source = bootstrap.render(
        ssh_public_key="other",
        hold_seconds=60,
        tunnel_hostname="box.example.com",
        tunnel_id="t",
        tunnel_credentials='{"x": 1}',
    )
    code, out, _ = kdev(capsys, "workspace", "use", "alice/box")
    cfg = config.load()
    assert code == 0 and cfg.notebook == "alice/box" and cfg.group_slug == "crew"
    assert cfg.tunnel_hostname == "box.example.com" and "taken from the notebook" in out


def test_workspace_create_slugifies_and_refuses_to_overwrite(home, kaggle, capsys, monkeypatch):
    cfg = config.Config()
    cfg.add("alice", config.Profile("alice"))
    config.save(cfg)
    monkeypatch.setattr(api, "get_kernel", lambda c, s: {})  # nothing exists yet
    code, _, _ = kdev(capsys, "workspace", "create", "--group", "crew", "--name", "My Box")
    assert code == 0 and kaggle.saved[-1]["slug"] == "alice/my-box"
    assert config.load().notebook == "alice/my-box"

    monkeypatch.setattr(api, "get_kernel", lambda c, s: {"id": 7})  # now it does
    code, _, err = kdev(capsys, "workspace", "create", "--group", "crew", "--name", "My Box")
    assert code == 1 and "already exists" in err


# --- the box ------------------------------------------------------------------


READY_LOG = [
    "KDEV_SESSION id=99 by=alice",
    "KDEV_STAGE sshd",
    "KDEV_STAGE env",
    "KDEV_STAGE repo",
    "KDEV_STAGE tunnel",
    "KDEV_READY host=box.example.com user=root",
]


def test_up_starts_restores_and_reports_ready(home, kaggle, capsys, monkeypatch):
    configured()
    kaggle.logs = READY_LOG
    restored = []
    monkeypatch.setattr(persistence, "plan_layers", lambda c, nb, latest: ["v3"])
    monkeypatch.setattr(
        session,
        "restore",
        lambda c, nb, alias, layers, note=None: (
            restored.append(layers) or {"restored": True, "done": 1, "total": 1}
        ),
    )
    code, out, err = kdev(capsys, "up", "-y")
    assert code == 0, err
    sent = kaggle.saved[-1]
    assert sent["machine_shape"] == "NvidiaTeslaT4" and sent["timeout_seconds"] == 9 * 3600
    assert '"restore_layers": ["v3"]' in sent["source"].replace('\\"', '"')
    assert restored == [["v3"]] and "ready" in out and "box.example.com" in out
    assert "s3cret" not in out + err, "tunnel credentials must never be printed"


def test_up_connects_to_a_running_box_instead_of_starting_another(
    home, kaggle, capsys, monkeypatch
):
    configured()
    kaggle.status = "RUNNING"
    monkeypatch.setattr(session, "reachable", lambda alias, timeout=10: True)
    code, out, _ = kdev(capsys, "up", "-y")
    assert code == 0 and kaggle.saved == [] and "already running" in out


def test_up_without_a_workspace_says_how_to_get_one(home, kaggle, capsys):
    cfg = config.Config(ssh_public_key="k")
    cfg.add("alice", config.Profile("alice"))
    config.save(cfg)
    code, _, err = kdev(capsys, "up", "-y")
    assert code == 1 and "kdev workspace join" in err


def test_up_that_never_gets_a_tunnel_says_so_and_how_to_stop_it(home, kaggle, capsys, monkeypatch):
    configured()
    kaggle.logs = ["KDEV_SESSION id=99 by=alice", "KDEV_STAGE sshd", "Traceback: boom"]
    monkeypatch.setattr(persistence, "plan_layers", lambda *a: [])
    statuses = iter(["COMPLETE", "RUNNING", "RUNNING"])
    monkeypatch.setattr(api, "session_status", lambda c, s: {"status": next(statuses)})
    code, _, err = kdev(capsys, "up", "-y")
    assert code == 1 and "never reported a tunnel" in err
    assert "kdev down --session-id 99" in err


def test_a_one_off_flag_never_becomes_the_default_box(home, kaggle, capsys, monkeypatch):
    configured()
    kaggle.logs = READY_LOG
    monkeypatch.setattr(persistence, "plan_layers", lambda *a: [])
    assert kdev(capsys, "up", "-y", "--gpu", "none", "--hours", "1")[0] == 0
    cfg = config.load()
    assert (cfg.default_gpu, cfg.default_hours) == ("t4", 9.0)


def test_status_json(home, kaggle, capsys):
    configured()
    kaggle.status = "RUNNING"
    code, out, _ = kdev(capsys, "status", "--json")
    info = json.loads(out)
    assert code == 0 and info["running"] is True and info["reachable"] is False
    assert kdev(capsys, "ps", "--json")[0] == 0  # the hidden alias


def test_down_when_nothing_is_running(home, kaggle, capsys, monkeypatch):
    configured()
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 255})())
    code, out, _ = kdev(capsys, "down")
    assert code == 0 and "nothing running" in out and kaggle.cancelled == []


def test_down_cancels_an_unreachable_box_as_whoever_started_it(home, kaggle, capsys, monkeypatch):
    """Measured: Kaggle refuses a cancel from any account but the starter."""
    configured()
    kaggle.status = "RUNNING"
    kaggle.owner_of_session = "bob"
    kaggle.logs = ["KDEV_SESSION id=99 by=bob"]
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 255})())
    code, out, _ = kdev(capsys, "down")
    assert code == 0 and kaggle.cancelled == [("bob", 99)] and "as bob" in out


def test_down_says_who_can_stop_it_when_they_are_not_here(home, kaggle, capsys, monkeypatch):
    configured()
    kaggle.status = "RUNNING"
    kaggle.owner_of_session = "carol"
    kaggle.logs = ["KDEV_SESSION id=99 by=carol"]
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 255})())
    code, _, err = kdev(capsys, "down")
    assert code == 1 and "Only carol can cancel" in err


def test_down_twice_leaves_a_saving_box_alone(home, kaggle, capsys, monkeypatch):
    """After `kdev down` Kaggle says RUNNING for as long as the save takes, with
    the tunnel gone. A second `down` must see it is stopping, not cancel it."""
    configured()
    kaggle.status = "RUNNING"
    kaggle.owner_of_session = "bob"
    kaggle.logs = ["KDEV_SESSION id=99 by=bob", "KDEV_READY host=h", "KDEV_STOP requested"]
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 255})())
    code, out, _ = kdev(capsys, "down")
    assert code == 0 and not kaggle.cancelled and "already stopping" in out


def test_status_calls_a_saving_box_stopping(home, kaggle, capsys, monkeypatch):
    configured()
    kaggle.status = "RUNNING"
    kaggle.logs = ["KDEV_READY host=h", "KDEV_DONE"]
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 255})())
    code, out, _ = kdev(capsys, "status", "--json")
    assert code == 0 and json.loads(out)["stopping"] is True


def test_down_keeps_the_ssh_block_for_the_next_box(home, kaggle, capsys, monkeypatch):
    """Measured: `kdev down` removed the block, so after another machine started
    the next box, status, backup, VS Code and `ssh kaggle` here could not reach
    it. A named tunnel's hostname is where every box will be."""
    configured()
    sshcfg.write("kaggle", "box.example.com")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())
    assert kdev(capsys, "down")[0] == 0 and sshcfg.has_block()


def test_status_writes_the_ssh_block_on_a_machine_without_one(home, kaggle, capsys, monkeypatch):
    configured()
    kaggle.status = "COMPLETE"
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 255})())
    assert not sshcfg.has_block()
    assert kdev(capsys, "status")[0] == 0 and sshcfg.has_block()


def test_logs_skip_heartbeats_and_stop_after_n(home, kaggle, capsys):
    configured()
    kaggle.logs = ["one", "KDEV_ALIVE seconds_left=10", "two", "three"]
    code, out, _ = kdev(capsys, "logs", "-n", "2")
    assert code == 0 and out.split() == ["one", "two"]


def test_restore_rejects_a_bad_version_and_needs_a_box(home, kaggle, capsys):
    configured()
    assert kdev(capsys, "restore", "--from", "12")[0] == 1
    code, _, err = kdev(capsys, "restore")
    assert code == 1 and "Could not read the box" in err


def test_ssh_with_no_known_host_explains(home, kaggle, capsys):
    cfg = configured()
    cfg.tunnel_hostname = ""
    config.save(cfg)
    code, _, err = kdev(capsys, "ssh")
    assert code == 1 and "kdev up" in err


# --- failures are always rendered, never raw ----------------------------------


def test_a_kaggle_permission_error_has_a_hint_not_json(home, kaggle, capsys, monkeypatch):
    configured()

    def denied(c, s):
        raise api.KaggleError("GetKernelSessionStatus: Permission denied (HTTP 403)", 403)

    monkeypatch.setattr(api, "session_status", denied)
    code, _, err = kdev(capsys, "up", "-y")
    assert code == 1 and "not allowed" in err and "{" not in err


def test_an_unexpected_crash_is_one_line_and_leaks_nothing(home, kaggle, capsys, monkeypatch):
    configured()

    def crash(c, s):
        token = c.token  # noqa: F841 - a secret in a local, as a real crash would have
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(api, "session_status", crash)
    code, out, err = kdev(capsys, "status")
    assert code == 1 and "unexpected error" in err and "Traceback" not in err
    assert "token-of-" not in out + err


def test_a_commands_own_exit_status_reaches_the_shell(home, kaggle, capsys, monkeypatch):
    """`kdev doctor` failing, or `kdev ssh -- false`, must not exit 0: scripts
    and CI branch on it."""
    configured()
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 7})())
    sshcfg.write("kaggle", "box.example.com")
    assert kdev(capsys, "ssh", "--", "false")[0] == 7


def test_restore_uses_the_boxs_own_notebook_not_this_machines(home, kaggle, capsys, monkeypatch):
    """Measured bug: a box running notebook B, restored from a machine pointed
    at notebook A, got A's "v8" -- and was then marked complete, orphaning B's
    real files. Version labels only mean something for the notebook that saved
    them."""
    configured()  # this machine points at alice/box
    monkeypatch.setattr(
        persistence,
        "box_state",
        lambda alias: {"layers": ["v8"], "notebook": "alice/other", "restored": False},
    )
    used = []
    monkeypatch.setattr(
        session,
        "restore",
        lambda c, nb, alias, layers, note=None: (
            used.append((nb, layers)) or {"restored": True, "done": 1, "total": 1}
        ),
    )
    code, out, _ = kdev(capsys, "restore")
    assert code == 0 and used == [("alice/other", ["v8"])]
    assert "belongs to alice/other" in out


def test_the_box_records_its_notebook_at_boot():
    from kdev import bootstrap

    src = bootstrap.render(
        ssh_public_key="k",
        tunnel_token="",
        tunnel_hostname="",
        hold_seconds=60,
        notebook="alice/box",
    )
    assert '"notebook": "alice/box"' in src.replace('\\"', '"')
    assert 'CFG.get("notebook", "")' in src


def test_only_what_was_prompted_becomes_a_default(home, kaggle, capsys, monkeypatch):
    """Review finding: one shared "picked" flag meant `kdev up --gpu none` on a
    machine with no saved length saved CPU as the default too."""
    from kdev import ui

    cfg = configured()
    cfg.default_gpu, cfg.default_hours = "t4", 0.0  # a gpu default, no length yet
    config.save(cfg)
    kaggle.logs = READY_LOG
    monkeypatch.setattr(persistence, "plan_layers", lambda *a: [])
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "pick_hours", lambda gpu, cap: 4.0)  # the only prompt
    monkeypatch.setattr(ui, "choose", lambda q, options: "none")  # "Open it?" -> no
    code, _, err = kdev(capsys, "up", "--gpu", "none")
    assert code == 0, err
    cfg = config.load()
    assert cfg.default_hours == 4.0, "the answered prompt is remembered"
    assert cfg.default_gpu == "t4", "the one-off --gpu is not"


def test_a_command_picked_from_the_menu_keeps_its_exit_status(home, kaggle, capsys, monkeypatch):
    """Review finding: the overview ran picked commands and dropped their
    exit status, so a failing `doctor` or ssh session exited 0."""
    from kdev import ui

    configured()
    kaggle.status = "RUNNING"
    monkeypatch.setattr(session, "reachable", lambda alias, timeout=10: True)
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "choose", lambda q, options: "ssh")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 5})())
    sshcfg.write("kaggle", "box.example.com")
    assert kdev(capsys)[0] == 5


def test_up_just_starts_when_the_running_session_ends_while_it_looks(
    home, kaggle, capsys, monkeypatch
):
    """Review finding: a session that finished during the (up to 15 min) look
    for a box was still "stopped", which failed with a wrong error."""
    configured()
    kaggle.logs = READY_LOG
    monkeypatch.setattr(persistence, "plan_layers", lambda *a: [])
    statuses = iter(["RUNNING", "COMPLETE"])
    monkeypatch.setattr(api, "session_status", lambda c, s: {"status": next(statuses, "RUNNING")})
    monkeypatch.setattr(session, "find_live_box", lambda *a: "")
    stopped = []
    monkeypatch.setattr("kdev.commands.box._stop_and_wait", lambda *a: stopped.append(a))
    code, _, err = kdev(capsys, "up", "-y")
    assert code == 0, err
    assert stopped == [] and kaggle.saved, "nothing to stop, so it simply started"


# --- first run and signing in -------------------------------------------------


def _no_prompt(*_a, **_k):
    raise AssertionError("setup asked a question it should not have")


def test_setup_on_a_second_machine_takes_the_tunnel_and_asks_nothing_twice(
    home, kaggle, capsys, monkeypatch
):
    """The first thing a new user runs. A second machine joining a workspace
    must get the tunnel from the notebook (no Cloudflare setup), and running
    setup again must change nothing and ask nothing."""
    from pathlib import Path

    from kdev import bootstrap, cloudflared, ui, wizard
    from kdev import notebook as nb

    monkeypatch.setattr(config, "default_ssh_public_key", lambda: "ssh-ed25519 AAAA m2@laptop")
    monkeypatch.setattr(cloudflared, "find", lambda: Path("/usr/local/bin/cloudflared"))
    monkeypatch.setattr(cloudflared, "version", lambda p: "cloudflared version 2026.9.1")
    monkeypatch.setattr(auth, "login", lambda name: "alice")
    monkeypatch.setattr(ui, "ask", lambda q, default="", secret=False: "alice")

    def join(cfg, creds, owner):
        cfg.notebook, cfg.notebook_id, cfg.group_slug = "alice/box", 42, "crew"
        return True

    monkeypatch.setattr(nb, "join", join)
    kaggle.source = bootstrap.render(
        ssh_public_key="m1",
        hold_seconds=60,
        tunnel_hostname="box.example.com",
        tunnel_id="t",
        tunnel_credentials='{"x": 1}',
    )
    monkeypatch.setattr(wizard, "choose_tunnel", _no_prompt)

    code, out, err = kdev(capsys, "setup")
    assert code == 0, err
    cfg = config.load()
    assert list(cfg.profiles) == ["alice"] and cfg.notebook == "alice/box"
    assert cfg.tunnel_hostname == "box.example.com" and "from the notebook" in out
    assert cfg.ssh_public_key == "ssh-ed25519 AAAA m2@laptop"

    # Again: everything is in place, so nothing is asked and nothing changes.
    monkeypatch.setattr(ui, "ask", _no_prompt)
    monkeypatch.setattr(nb, "join", _no_prompt)
    monkeypatch.setattr(auth, "login", _no_prompt)
    before = config.CONFIG_PATH.read_text()
    assert kdev(capsys, "setup")[0] == 0
    assert config.CONFIG_PATH.read_text() == before


def test_signing_the_same_kaggle_user_in_twice_is_refused(home, kaggle, capsys, monkeypatch):
    """Two names for one Kaggle account would show its quota twice and let the
    picker "switch" to the same quota."""
    configured()  # alice and bob
    forgotten = []
    monkeypatch.setattr(auth, "login", lambda name: "alice")
    monkeypatch.setattr(auth, "forget", forgotten.append)
    code, _, err = kdev(capsys, "account", "add", "alice2")
    assert code == 1 and "already signed in here as 'alice'" in err
    assert forgotten == ["alice2"] and "alice2" not in config.load().profiles


def test_re_signing_as_the_wrong_user_never_touches_the_working_token(
    home, kaggle, capsys, monkeypatch
):
    """The refresh lands in a scratch slot: if the browser was signed in as
    someone else, the account's current token must still be there."""
    configured()
    creds = auth.creds_dir()
    creds.mkdir(parents=True, exist_ok=True)
    (creds / "alice.json").write_text("alice's working token")

    def login(name):
        (creds / f"{name}.json").write_text("someone else's token")
        return "mallory"

    monkeypatch.setattr(auth, "login", login)
    code, _, err = kdev(capsys, "account", "add", "alice")
    assert code == 1 and "signed in as mallory, not alice" in err
    assert (creds / "alice.json").read_text() == "alice's working token"
    assert not (creds / "alice.signing-in.json").exists()


def test_re_signing_as_the_right_user_replaces_the_token(home, kaggle, capsys, monkeypatch):
    configured()
    creds = auth.creds_dir()
    creds.mkdir(parents=True, exist_ok=True)
    (creds / "alice.json").write_text("old")

    def login(name):
        (creds / f"{name}.json").write_text("fresh")
        return "alice"

    monkeypatch.setattr(auth, "login", login)
    assert kdev(capsys, "account", "add", "alice")[0] == 0
    assert (creds / "alice.json").read_text() == "fresh"


def test_json_output_is_clean_even_when_something_warns(home, kaggle, capsys):
    """`kdev status --json | jq` must get JSON and only JSON on stdout."""
    configured()
    code, out, _ = kdev(capsys, "status", "--json")
    assert code == 0 and json.loads(out)["notebook"] == "alice/box"


def test_up_starts_a_notebook_that_has_never_run_and_keeps_its_title(
    home, kaggle, capsys, monkeypatch
):
    """The reported case: a notebook made in the Kaggle editor, joined with
    `kdev workspace join`, never run. `kdev up` must start it -- and must not
    rename "v0.1.0_testing" to its slug while doing so."""
    configured()
    kaggle.status = api.NEVER_RUN
    kaggle.logs = READY_LOG
    monkeypatch.setattr(
        api,
        "get_kernel",
        lambda c, s: {"id": 42, "title": "v0.1.0_testing", "currentVersionNumber": None},
    )
    code, out, err = kdev(capsys, "up", "-y")
    assert code == 0, err
    assert kaggle.saved[-1]["title"] == "v0.1.0_testing"
    assert "ready" in out


def test_status_of_a_notebook_that_has_never_run(home, kaggle, capsys):
    configured()
    kaggle.status = api.NEVER_RUN
    code, out, _ = kdev(capsys, "status")
    assert code == 0 and "never run yet" in out and "nothing saved yet" in out
