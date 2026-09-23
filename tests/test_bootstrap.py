import ast
import json
import os
import re

import pytest

from kdev import bootstrap


def test_bootstrap_renders_valid_python_with_injected_config():
    src = bootstrap.render(
        ssh_public_key="ssh-ed25519 AAAAC3Nza test@host",
        tunnel_token="tok-123",
        tunnel_hostname="kaggle.example.com",
        hold_seconds=3600,
        git_remote="git@github.com:me/repo.git",
        git_deploy_key="-----BEGIN KEY-----\nabc\n-----END KEY-----",
    )
    ast.parse(src)  # Kaggle would just fail the run; catch it here instead.
    assert "tok-123" in src
    assert bootstrap.READY_MARKER in src
    # A multi-line deploy key must survive embedding without breaking the literal.
    assert "BEGIN KEY" in src


@pytest.mark.parametrize(
    "hostile",
    [
        'a"""b',  # would close the literal if quotes were not escaped
        "back\\slash\\",  # trailing backslash before the closing quotes
        "quote\" and 'quote",  # bare quotes of both kinds
        "line\nbreak",
    ],
)
def test_bootstrap_survives_hostile_values_and_roundtrips_them(hostile):
    """Tokens and deploy keys are arbitrary bytes; embedding must not break."""
    src = bootstrap.render(
        ssh_public_key=hostile,
        tunnel_token=hostile,
        tunnel_hostname="h",
        hold_seconds=60,
    )
    tree = ast.parse(src)  # raises if the value escaped the literal
    blob = next(
        n.value.args[0].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "CFG"
    )
    assert json.loads(blob)["tunnel_token"] == hostile


def test_ready_marker_regex_matches_emitted_line():
    """The CLI greps for this exact shape; the two must not drift apart."""
    line = f"{bootstrap.READY_MARKER} host=kaggle.example.com user=root"
    m = re.search(rf"{bootstrap.READY_MARKER} host=(\S+)", line)
    assert m and m.group(1) == "kaggle.example.com"


def test_stage_markers_in_generated_script_match_the_local_stage_list():
    """Renderer and parser share these names; a drift means a dead progress bar."""
    src = bootstrap.render(ssh_public_key="k", tunnel_token="", tunnel_hostname="", hold_seconds=60)
    for key, _label in bootstrap.STAGES:
        assert f'stage("{key}")' in src, f"{key} is listed but never announced"


def test_stage_regex_parses_the_emitted_marker():
    line = f"{bootstrap.STAGE_MARKER} tunnel"
    m = re.search(rf"{bootstrap.STAGE_MARKER} (\w+)", line)
    assert m and m.group(1) == "tunnel"


def test_locally_managed_render_writes_ingress_and_uses_config_flag():
    """--token makes cloudflared ignore local config, so creds mode must not use it."""
    src = bootstrap.render(
        ssh_public_key="k",
        tunnel_credentials='{"AccountTag":"a","TunnelSecret":"s","TunnelID":"u"}',
        tunnel_id="uuid-1",
        tunnel_hostname="kaggle.example.com",
        hold_seconds=60,
    )
    ast.parse(src)
    assert "ingress" in src and "ssh://localhost:22" in src
    assert '"--config"' in src, "locally-managed run must pass --config"


def test_stop_file_lets_down_work_without_a_session_id():
    """Batch runs return no session id, and cancelling by slug is denied."""
    src = bootstrap.render(ssh_public_key="k", tunnel_hostname="", hold_seconds=60)
    assert "/kaggle/working/.kdev-stop" in src
    assert "KDEV_STOP" in src


def test_generated_source_always_carries_the_marker():
    """The marker is how kdev recognises its own notebooks; losing it re-opens the bug."""
    src = bootstrap.render(ssh_public_key="k", tunnel_hostname="", hold_seconds=60)
    assert src.startswith(bootstrap.MARKER)


def _nb(*sources):
    return json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": s,
                }
                for s in sources
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 4,
        }
    )


def test_injection_keeps_every_user_cell():
    """The bug was destroying these; they must survive untouched."""
    src = bootstrap.render(ssh_public_key="k", tunnel_hostname="", hold_seconds=60)
    out, kt = bootstrap.inject(_nb("import torch\n", "model.fit()\n"), src)
    cells = json.loads(out)["cells"]
    assert kt == "notebook"
    assert len(cells) == 3
    assert bootstrap.MARKER in "".join(cells[0]["source"])
    assert "".join(cells[1]["source"]) == "import torch\n"
    assert "".join(cells[2]["source"]) == "model.fit()\n"


def test_reinjection_replaces_rather_than_stacks():
    """Every `kdev up` injects; without this the notebook grows a cell each time."""
    src = bootstrap.render(ssh_public_key="k", tunnel_hostname="", hold_seconds=60)
    out, _ = bootstrap.inject(_nb("user code\n"), src)
    for _ in range(3):
        out, _ = bootstrap.inject(out, src)
    cells = json.loads(out)["cells"]
    assert len(cells) == 2, "kdev cell must be replaced, not appended"
    assert "".join(cells[1]["source"]) == "user code\n"


def test_injection_into_a_plain_script_keeps_the_code_below():
    src = bootstrap.render(ssh_public_key="k", tunnel_hostname="", hold_seconds=60)
    out, kt = bootstrap.inject("import torch\nmodel.fit()\n", src)
    assert kt == "script"
    assert "model.fit()" in out
    assert out.count(bootstrap.SCRIPT_BEGIN) == 1
    again, _ = bootstrap.inject(out, src)
    assert again.count(bootstrap.SCRIPT_BEGIN) == 1 and "model.fit()" in again


def test_injection_into_an_empty_notebook_produces_valid_nbformat():
    src = bootstrap.render(ssh_public_key="k", tunnel_hostname="", hold_seconds=60)
    out, kt = bootstrap.inject("", src)
    nb = json.loads(out)
    assert kt == "notebook" and nb["nbformat"] == 4 and len(nb["cells"]) == 1


def test_injection_survives_malformed_json_without_losing_it():
    """A notebook we cannot parse must not be silently discarded."""
    src = bootstrap.render(ssh_public_key="k", tunnel_hostname="", hold_seconds=60)
    out, kt = bootstrap.inject("{not valid json", src)
    assert "{not valid json" in out and kt == "script"


def test_a_long_session_checkpoints_instead_of_only_saving_at_the_end():
    """Kaggle commits /kaggle/working once, when the run ends. Without a timer
    an infra fault at hour 7 of 9 takes everything with it."""
    src = bootstrap.render(
        ssh_public_key="k",
        tunnel_token="",
        tunnel_hostname="",
        hold_seconds=9 * 3600,
        git_remote="git@example.com:me/ws.git",
    )
    assert "CHECKPOINT_SECONDS" in src
    assert "KDEV_CHECKPOINT" in src
    # and it only runs when there is somewhere to push
    assert "if repo and time.time() >= next_checkpoint:" in src


def test_the_notebook_carries_the_workspace_settings_but_never_a_machines_key():
    src = bootstrap.render(
        ssh_public_key="ssh-ed25519 AAAA machine-a",
        hold_seconds=60,
        tunnel_hostname="box.example.com",
        tunnel_id="t",
        tunnel_credentials='{"AccountTag":"a","TunnelSecret":"s","TunnelID":"t"}',
        git_remote="git@example.com:me/ws.git",
    )
    nb, _ = bootstrap.inject("", src)
    for source in (src, nb):
        got = bootstrap.extract_config(source)
        assert got["tunnel_hostname"] == "box.example.com"
        assert got["git_remote"] == "git@example.com:me/ws.git"
        assert "ssh_public_key" not in got
    assert bootstrap.extract_config("print('not kdev')") == {}
    assert bootstrap.extract_config('{"cells": "garbage"}') == {}


def test_the_box_gets_its_restore_code_and_plan_at_boot():
    """Shipped inside the notebook source, so the box can record its state and
    authorise keys before any laptop has talked to it."""
    src = bootstrap.render(
        ssh_public_key="k",
        tunnel_token="",
        tunnel_hostname="",
        hold_seconds=60,
        restore_layers=["v3", "v4"],
    )
    m = re.search(r'CFG = json\.loads\(r"""(.*?)"""\)', src, re.DOTALL)
    cfg = json.loads(m.group(1))
    assert cfg["box_script"] == bootstrap.BOX_SCRIPT.read_text()
    assert cfg["restore_layers"] == ["v3", "v4"]
    main = src[src.index("def main():") :]
    # state is recorded before the slow steps, not after
    assert main.index("init_state") < main.index('stage("sshd")')
    assert "record_meta" in main and "KDEV_SESSION" in main


def test_session_id_is_read_from_the_container_name():
    """Measured: kaggle_<token>-352003858-webtier. kdev needs the id to cancel
    a box it can no longer reach."""
    src = bootstrap.render(ssh_public_key="k", tunnel_token="", tunnel_hostname="", hold_seconds=60)
    ns: dict = {"os": __import__("os")}
    body = src[src.index("def session_id():") : src.index("def install_sshd(box):")]
    exec(body, ns)
    old = os.environ.get("KAGGLE_CONTAINER_NAME")
    try:
        os.environ["KAGGLE_CONTAINER_NAME"] = "kaggle_container-352003858-webtier"
        assert ns["session_id"]() == "352003858"
        os.environ["KAGGLE_CONTAINER_NAME"] = "something-else"
        assert ns["session_id"]() == ""
    finally:
        if old is None:
            os.environ.pop("KAGGLE_CONTAINER_NAME", None)
        else:
            os.environ["KAGGLE_CONTAINER_NAME"] = old


def test_nothing_below_the_kdev_cell_ever_runs():
    """Measured on a real notebook: returning from the kdev cell let Save & Run
    All carry on into the user's own cells after every `kdev down`, rewriting
    files that had just been saved (and, for a training cell, burning quota
    after the box was 'off'). The teardown must end the process instead."""
    src = bootstrap.render(ssh_public_key="k", tunnel_token="", tunnel_hostname="", hold_seconds=60)
    finally_block = src[src.rindex("    finally:") : src.rindex("\nmain()")]
    assert "end_run()" in finally_block
    # and only after everything that must be saved has been
    for step in ("record_meta", "snapshot(repo)", "KDEV_DONE"):
        assert finally_block.index(step) < finally_block.index("end_run()")


def test_end_run_blanks_later_notebook_cells_and_hard_exits_a_script():
    """In a notebook kernel, killing the process marks the version ERROR, so
    later cells are blanked instead; a script has nothing to blank."""
    src = bootstrap.render(ssh_public_key="k", tunnel_token="", tunnel_hostname="", hold_seconds=60)
    body = src[src.index("def end_run():") : src.index("CHECKPOINT_SECONDS = ")]

    class FakeIPython:
        def __init__(self):
            self.input_transformers_cleanup = []

    ip = FakeIPython()
    ns = {"os": None, "sys": None, "get_ipython": lambda: ip}
    exec(body, ns)
    ns["end_run"]()
    [blank] = ip.input_transformers_cleanup
    assert blank(["!touch whosokai.py\n"]) == []
    assert blank(["%%writefile x.py\n", "print(1)\n"]) == []

    exited = []
    fake_os = type("O", (), {"_exit": staticmethod(exited.append)})
    fake_sys = type(
        "S",
        (),
        {
            "stdout": type("F", (), {"flush": lambda: None}),
            "stderr": type("F", (), {"flush": lambda: None}),
        },
    )
    ns = {"os": fake_os, "sys": fake_sys}  # no get_ipython: a plain script
    exec(body, ns)
    ns["end_run"]()
    assert exited == [0]


@pytest.mark.parametrize(
    "existing",
    [
        "",  # a notebook kdev creates
        json.dumps({"cells": [], "metadata": {}}),  # one with no kernelspec
        json.dumps(
            {"cells": [], "metadata": {"kernelspec": {"name": "python3", "display_name": "Mine"}}}
        ),
    ],
)
def test_every_notebook_kdev_writes_can_be_started_by_kaggle(existing):
    """Measured: Kaggle's papermill runner fails a notebook with no kernelspec
    before the first cell runs -- the session dies instantly."""
    src, kind = bootstrap.inject(existing, "print(1)")
    assert kind == "notebook"
    meta = json.loads(src)["metadata"]
    assert meta["kernelspec"]["name"] == "python3"
    if "Mine" in existing:
        assert meta["kernelspec"]["display_name"] == "Mine"  # never overwritten


def test_quick_tunnel_render_has_no_hostname_and_scrapes_the_log():
    src = bootstrap.render(ssh_public_key="k", tunnel_hostname="", hold_seconds=60)
    ast.parse(src)
    assert "wait_for_quick_tunnel_host" in src
    assert "trycloudflare.com" in src
