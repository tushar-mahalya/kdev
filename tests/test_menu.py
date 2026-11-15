"""The home menu: keys pick, disabled items refuse, quitting is not an error."""

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kdev import menu


def _items():
    return [
        menu.Item("1", "Start the box", ("up",)),
        menu.Item("2", "Open a shell", ("ssh",), disabled="no box is running"),
        menu.Item("3", "Stop the box", ("down",)),
        menu.Item("s", "Status", ("status",), group="more"),
    ]


def _run(keys: str):
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        picker = menu.Menu(
            _items, lambda width: [("", "dashboard")], input=pipe, output=DummyOutput()
        )
        return picker.run(), picker


@pytest.mark.parametrize(
    "keys,expected",
    [
        ("\r", "up"),
        ("3", "down"),
        ("s", "status"),
        ("S", "status"),
        ("j\r", "down"),
        ("jj\r", "status"),
    ],
)
def test_keys_pick_items(keys, expected):
    item, _ = _run(keys)
    assert item is not None and item.argv[0] == expected


def test_arrows_skip_disabled_items_and_wrap():
    item, _ = _run("\x1b[B\x1b[B\x1b[B\r")  # down x3 from 1: 3, s, back to 1
    assert item is not None and item.argv == ("up",)


def test_a_disabled_item_explains_itself_instead_of_running():
    item, picker = _run("2q")
    assert item is None
    assert "no box is running" in picker.flash


@pytest.mark.parametrize("keys", ["q", "\x03", "\x04"])
def test_quitting_returns_nothing(keys):
    item, _ = _run(keys)
    assert item is None


def test_each_item_shows_the_command_it_runs():
    assert menu.Item("1", "x", ("up", "--gpu", "none")).command == "kdev up --gpu none"
    assert menu.Item("q", "Quit").command == ""


def test_live_slots_fill_from_threads_and_record_failures():
    import threading

    live = menu.Live()
    done = threading.Event()
    calls = []

    def changed() -> None:
        calls.append(1)
        if len(calls) == 2:
            done.set()

    live.changed = changed  # pyright: ignore[reportAttributeAccessIssue]
    live.load("ok", lambda: 42)
    live.load("bad", lambda: 1 / 0)
    assert done.wait(5)
    assert live.get("ok") == 42 and live.ready("bad") and live.get("bad") is None
    assert "division" in live.errors["bad"]


@pytest.mark.parametrize("rows,folded", [(14, True), (40, False)])
def test_a_short_terminal_keeps_every_key_on_one_line(monkeypatch, rows, folded):
    picker = menu.Menu(_items, lambda width: [])
    monkeypatch.setattr(picker, "_height", lambda: rows)
    text = "".join(f[1] for f in picker._render_menu())
    assert ("more" in text) is folded
    assert "s" in text
    assert ("Status" in text) is not folded
