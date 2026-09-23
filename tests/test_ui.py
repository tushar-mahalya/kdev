from unittest.mock import patch

import pytest


def test_stages_advance_marks_previous_done_and_never_duplicates():
    from kdev import ui

    st = ui.Stages([("a", "A"), ("b", "B"), ("c", "C")])
    st.advance("a")
    st.advance("b")
    assert st.done == ["a"] and st.current == "b"
    st.advance("b")  # a repeated marker must not double-count
    assert st.done == ["a"]
    st.finish()
    assert st.done == ["a", "b"]
    st.finish()  # idempotent
    assert st.done == ["a", "b"]


@pytest.mark.parametrize(
    "fraction,expected", [(0.0, "red"), (0.1, "red"), (0.3, "yellow"), (0.9, "green")]
)
def test_quota_bar_colour_signals_headroom(fraction, expected):
    from kdev import ui

    assert ui.bar(fraction).style == expected


def test_quota_bar_clamps_out_of_range_input():
    from kdev import ui

    assert len(ui.bar(5.0, width=10).plain) == 10
    assert len(ui.bar(-1.0, width=10).plain) == 10


def test_a_failed_stage_is_marked_failed_and_keeps_its_reason():
    """The board must not end on a tick when the step did not succeed, and the
    reason is the whole point of the line -- the final frame has to keep it."""
    from rich.console import Console

    from kdev import ui

    st = ui.Stages([("a", "A"), ("b", "B")])
    st.advance("a")
    st.advance("b")
    st.fail("cloudflared exited")

    out = Console(width=60, record=True, theme=ui.THEME)
    out.print(st._render(final=True))
    text = out.export_text()
    assert ui.g("err") in text and "cloudflared exited" in text
    assert "b" not in st.done  # a failed stage never counts as done
    assert st.done == ["a"]


def test_every_started_stage_reports_a_clock():
    """A blank in the elapsed column reads as a missing measurement, not as a
    step that was quick."""
    from rich.console import Console

    from kdev import ui

    st = ui.Stages([("a", "A"), ("b", "B"), ("c", "C")])
    st.advance("a")
    st.advance("b")
    out = Console(width=60, record=True, theme=ui.THEME)
    out.print(st._render())
    lines = [ln for ln in out.export_text().splitlines() if ln.strip()]
    assert lines[0].rstrip().endswith("s")  # A ran
    assert lines[1].rstrip().endswith("s")  # B is running
    assert not lines[2].rstrip().endswith("s")  # C has not started


def test_an_unknown_stage_marker_is_ignored_rather_than_crashing():
    """The remote side also prints lines that are not stages."""
    from kdev import ui

    st = ui.Stages([("a", "A")])
    st.advance("a")
    st.advance("something-else")
    assert st.current == "a"


def test_elapsed_is_readable_past_a_minute():
    from kdev import ui

    assert ui._fmt_elapsed(9) == "9s"
    assert ui._fmt_elapsed(59) == "59s"
    assert ui._fmt_elapsed(60) == "1m00s"
    assert ui._fmt_elapsed(605) == "10m05s"


def test_glyphs_fall_back_to_ascii_on_a_terminal_that_cannot_draw_them(monkeypatch):
    """A mojibake tick in a status line is worse than a plain one."""
    from kdev import ui

    monkeypatch.setattr(ui, "_ascii_only", lambda: True)
    assert ui.g("ok") == "+" and ui.g("err") == "x"
    assert ui.bar(0.5, width=10).plain == "#####....."
    assert set(ui._spinner_frames()) <= set("|/-\\")

    monkeypatch.setattr(ui, "_ascii_only", lambda: False)
    assert ui.g("ok") == "✓"


def test_no_colour_is_honoured(monkeypatch):
    from kdev import ui

    monkeypatch.setenv("NO_COLOR", "1")
    assert ui._no_colour()
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert ui._no_colour()


def test_a_bad_custom_hours_answer_is_rejected_not_coerced(monkeypatch):
    from kdev import ui

    monkeypatch.setattr(ui, "select", lambda *a, **k: 0.0)
    monkeypatch.setattr(
        ui.questionary, "text", lambda *a, **k: type("A", (), {"ask": lambda _s: "soon"})()
    )
    from kdev.errors import KdevError

    with pytest.raises(KdevError):
        ui.pick_hours("t4", 12.0)


def test_the_banner_never_breaks_a_hostname_across_lines():
    """A wrapped hostname cannot be copied, which is the only reason it is there."""
    from rich.console import Console

    from kdev import ui

    context = [("account", "alice"), ("notebook", "alice/box"), ("tunnel", "box.example.com")]
    for width in (40, 60, 100, 200):
        out = Console(width=width, record=True, theme=ui.THEME)
        with patch.object(ui, "console", out):
            ui.header(context=context)
        text = out.export_text()
        assert "box.example.com" in text, width
