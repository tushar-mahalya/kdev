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


def test_header_shows_compact_multicolour_logo_in_a_colour_terminal(monkeypatch):
    from io import StringIO

    from rich.console import Console

    from kdev import ui

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    output = StringIO()
    terminal = Console(
        file=output,
        width=ui._LOGO_WIDTH + ui.GUTTER,
        force_terminal=True,
        color_system="truecolor",
        no_color=False,
        record=True,
        theme=ui.THEME,
    )
    monkeypatch.setattr(ui, "console", terminal)
    ui.header("up", [("tunnel", "box.example.com")])

    text = terminal.export_text()
    lines = text.splitlines()
    assert len(lines[: lines.index("")]) == 5
    assert all(len(line) <= terminal.width for line in lines)
    assert any(char in text for char in "█▀▄")
    assert "up  ·  v" in text and "box.example.com" in text
    for rgb in ("0;184;212", "67;213;237", "33;142;255"):
        assert rgb in output.getvalue()


@pytest.mark.parametrize("limited", ["pipe", "narrow", "ascii", "no_color", "dumb", "no_system"])
def test_header_uses_plain_title_when_pixel_art_cannot_render(monkeypatch, limited):
    from io import StringIO

    from rich.console import Console

    from kdev import ui

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb" if limited == "dumb" else "xterm")
    monkeypatch.setattr(ui, "_ascii_only", lambda: limited == "ascii")
    if limited == "no_color":
        monkeypatch.setenv("NO_COLOR", "1")
    terminal = Console(
        file=StringIO(),
        width=ui._ICON_WIDTH + ui.GUTTER - 1 if limited == "narrow" else 80,
        force_terminal=limited != "pipe",
        color_system=None if limited == "no_system" else "truecolor",
        no_color=limited == "no_color",
        record=True,
        theme=ui.THEME,
    )
    monkeypatch.setattr(ui, "console", terminal)
    ui.header("status")

    text = terminal.export_text()
    assert text.lstrip().startswith("kdev") and "status" in text
    assert not any(char in text for char in "█▀▄")


@pytest.mark.parametrize("width", [22, 40, 55])
def test_header_uses_compact_mascot_without_wrapping(monkeypatch, width):
    from io import StringIO

    from rich.console import Console

    from kdev import ui

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    terminal = Console(
        file=StringIO(),
        width=width,
        force_terminal=True,
        no_color=False,
        record=True,
        theme=ui.THEME,
    )
    monkeypatch.setattr(ui, "console", terminal)
    ui.header("status")

    lines = terminal.export_text().splitlines()
    compact = ui._logo(compact=True).plain.splitlines()
    assert len(lines[: lines.index("")]) == len(compact) == 4
    assert [line[ui.GUTTER :] for line in lines[:4]] == compact
    assert all(len(line) <= width for line in lines)
    assert "kdev · status" in "\n".join(lines)


def test_logo_uses_terminal_foreground_for_light_and_dark_themes():
    from io import StringIO

    from rich.console import Console
    from rich.terminal_theme import DEFAULT_TERMINAL_THEME, MONOKAI

    from kdev import ui

    terminal = Console(file=StringIO(), record=True, color_system="truecolor")
    terminal.print(ui._logo())
    for theme in (DEFAULT_TERMINAL_THEME, MONOKAI):
        html = terminal.export_html(theme=theme, inline_styles=True, clear=False)
        foreground = theme.foreground_color.hex
        assert f'<span style="color: {foreground}; text-decoration-color: {foreground}">' in html
        assert ui.ACCENT in html


def test_header_scope_suppresses_duplicate_logos_and_resets_after_errors(monkeypatch):
    from io import StringIO

    from rich.console import Console

    from kdev import ui

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    terminal = Console(
        file=StringIO(),
        width=80,
        force_terminal=True,
        no_color=False,
        record=True,
        theme=ui.THEME,
    )
    monkeypatch.setattr(ui, "console", terminal)
    with pytest.raises(RuntimeError), ui.command_header_scope():
        ui.header("setup")
        ui.header("up")
        raise RuntimeError("cancelled setup")
    with ui.command_header_scope():
        ui.header("status")

    text = terminal.export_text()
    assert text.count(ui._logo().plain.splitlines()[0].strip()) == 2
    assert "setup" in text and "up" in text and "status" in text


def test_terminal_only_header_leaves_piped_output_untouched(monkeypatch):
    from io import StringIO

    from rich.console import Console

    from kdev import ui

    output = StringIO()
    monkeypatch.setattr(ui, "console", Console(file=output, force_terminal=False))
    ui.header("config", terminal_only=True)
    assert output.getvalue() == ""


@pytest.mark.parametrize("width", [28, 40, 60, 80, 96, 160])
def test_responsive_tables_retain_every_value_and_account_status(monkeypatch, width):
    from io import StringIO

    from rich.console import Console
    from rich.text import Text

    from kdev import ui

    terminal = Console(
        file=StringIO(), width=width, force_terminal=True, record=True, theme=ui.THEME
    )
    monkeypatch.setattr(ui, "console", terminal)
    terminal.print(
        ui.table(
            [("setting", "left"), ("value", "left"), ("description", "left")],
            [("workspace", "research-team/long-lived-development-box", Text("Saved notebook"))],
        )
    )
    terminal.print(
        ui.accounts_table(
            [
                ui.ProfileRow("research", "alice", 97200, 108000, 72000, 72000, active=True),
                ui.ProfileRow(
                    "backup", "bob", 0, 108000, error="Sign in again: kdev account add backup"
                ),
            ]
        )
    )
    text = terminal.export_text()
    compact = "".join(text.split())
    for value in (
        "research-team/long-lived-development-box",
        "Saved notebook",
        "research",
        "alice",
        "27.0h",
        "20.0h",
        "backup",
        "bob",
        "Sign in again: kdev account add backup",
    ):
        assert "".join(value.split()) in compact.replace("—", ""), (width, value, text)
    assert ui.g("live") in text
    assert "…" not in text
    assert all(len(line) <= min(width, ui.MAX_WIDTH) for line in text.splitlines())


@pytest.mark.parametrize("width", [28, 40, 60, 96, 160])
def test_result_cards_keep_long_paths_and_copyable_commands(monkeypatch, width):
    from io import StringIO

    from rich.console import Console

    from kdev import ui

    terminal = Console(
        file=StringIO(), width=width, force_terminal=True, record=True, theme=ui.THEME
    )
    monkeypatch.setattr(ui, "console", terminal)
    saved = "/kaggle/working/research/checkpoints/final-model.safetensors"
    ui.card("Saved", [("files", saved), ("connect", ui.cmd("ssh kdev-demo"))])
    ui.next_steps([("kdev config set hours 9", "change the session length")])
    text = terminal.export_text()
    compact = "".join(line.strip(" │|") for line in text.splitlines())
    assert saved in compact
    assert "ssh kdev-demo" in text
    assert "kdevconfigsethours9" in "".join(compact.split())
    assert "…" not in text
    assert all(len(line) <= min(width, ui.MAX_WIDTH) for line in text.splitlines())


def test_terminal_stage_board_keeps_failure_pending_stages_and_timings(monkeypatch):
    from io import StringIO

    from rich.console import Console

    from kdev import ui

    terminal = Console(file=StringIO(), width=40, force_terminal=True, record=True, theme=ui.THEME)
    monkeypatch.setattr(ui, "console", terminal)
    stage = ui.Stages([("a", "Submit"), ("b", "Restore"), ("c", "Connect SSH")], head="research")
    stage.advance("a")
    stage.advance("b")
    stage.fail("Could not restore saved files")
    terminal.print(stage._render(final=True))
    text = terminal.export_text()
    compact = "".join(line.strip(" │|") for line in text.splitlines())
    assert "Couldnotrestoresavedfiles" in "".join(compact.split())
    assert ui.g("err") in text and ui.g("pending") in text
    assert "1/3 complete" in text and "Connect SSH" in text
    assert stage.done == ["a"]


def test_ascii_presentation_has_no_unicode_borders_or_spinners(monkeypatch):
    from io import StringIO

    from rich.console import Console

    from kdev import ui

    terminal = Console(file=StringIO(), width=80, force_terminal=True, record=True, theme=ui.THEME)
    monkeypatch.setattr(ui, "console", terminal)
    monkeypatch.setattr(ui, "_ascii_only", lambda: True)
    ui.header("status")
    ui.card("Ready", [("host", "box.example.com"), ("connect", ui.cmd("ssh kdev"))])
    terminal.print(ui.accounts_table([ui.ProfileRow("demo", "alice", 0, 0, error="Sign in")]))
    ui.next_steps([("kdev up", "start the box")])
    board = ui.Stages([("boot", "Start box")])
    board.advance("boot")
    terminal.print(board._render())
    terminal.export_text().encode("ascii")


@pytest.mark.parametrize("mode", ["no_color", "dumb", "console"])
def test_prompt_styles_override_questionary_default_colours(monkeypatch, mode):
    from io import StringIO

    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from rich.console import Console

    from kdev import ui

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb" if mode == "dumb" else "xterm")
    if mode == "no_color":
        monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(ui, "console", Console(file=StringIO(), no_color=mode == "console"))
    with create_pipe_input() as pipe:
        question = ui.questionary.select(
            "Choose", ["First"], style=ui._prompt_style(), input=pipe, output=DummyOutput()
        )
        assert question.application.style is not None
        for token in ("qmark", "answer", "pointer", "highlighted", "instruction", "search_success"):
            attrs = question.application.style.get_attrs_for_style_str(f"class:{token}")
            assert attrs.color == "default" and attrs.bgcolor == "default", (mode, token)


def test_prompt_redesign_preserves_values_defaults_secrets_and_cancellation(monkeypatch):
    from types import SimpleNamespace

    from kdev import ui
    from kdev.errors import Cancelled

    calls = []
    answers = iter(["keep", False, "  trimmed  ", " secret ", None])

    def question(kind):
        def make(message, **kwargs):
            calls.append((kind, message, kwargs))
            return SimpleNamespace(ask=lambda: next(answers))

        return make

    for kind in ("select", "confirm", "text", "password"):
        monkeypatch.setattr(ui.questionary, kind, question(kind))
    monkeypatch.setattr(ui, "interactive", lambda: True)
    choices = [ui.Choice("Keep files", value="keep"), ui.Choice("Replace files", value="replace")]
    assert ui.select("Files?", choices) == "keep"
    assert calls[0][2]["choices"] is choices
    assert [choice.value for choice in choices] == ["keep", "replace"]
    assert ui.confirm("Replace?", default=False) is False
    assert calls[1][2]["default"] is False
    assert ui.ask("Name", default="demo") == "trimmed"
    assert calls[2][2]["default"] == "demo"
    assert ui.ask("Token", secret=True) == "secret"
    assert "default" not in calls[3][2]
    with pytest.raises(Cancelled):
        ui.select("Files?", choices)
    monkeypatch.setattr(ui, "interactive", lambda: False)
    assert ui.confirm("Replace?", default=False) is False
    assert len(calls) == 5
