"""kdev's design system. Every command builds its output from these parts only.

Not a full-screen app, on purpose: `kdev up` ends by handing you a shell, and a
UI you have to quit first would be in the way. The references, and what each
contributed:

  Docker Compose  the status board -- a line per step, each with its own clock
  fly / vercel    the result card -- the few facts that matter, and the command
                  to copy
  gh              glyph-and-gutter status lines, and data on stdout
  Charm           a quiet palette where colour carries meaning, not decoration

Tokens
  accent  cyan    the brand, and commands you can run next
  ok      green   done, reachable, enough quota
  warn    yellow  needs attention, still works
  err     red     failed
  muted   grey    labels, secondary facts, hints
  bold            the values that matter: hosts, names, counts

Glyphs      ✓ done   ! attention   ✗ failed   ● live   ○ idle/pending   › step

Components
  header(title, facts)   pixel logo in colour terminals, then the command context
  ok / warn / err        a status line; `hint` sits indented beneath
  facts(rows)            aligned label/value pairs
  card(title, rows)      the command's result, with facts and next actions
  table(...)             restrained headers; labeled records on narrow terminals
  Stages                 the live board for long operations
  next_steps(cmds)       what to run next, commands in accent
  spinner(text)          "doing something…", always present tense and lowercase

Voice
  Sentence case. No trailing full stop on a one-line status. Labels lowercase.
  A command, when shown, is the literal thing to type. Durations like 9h or
  2m04s, clock times like 14:05. One result card per command.

It degrades: without a TTY, boards become plain lines and prompts never
appear; NO_COLOR is honoured; a terminal that cannot draw the glyphs gets ASCII.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Any, Protocol

import questionary
from questionary import Choice
from rich import box
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .errors import Cancelled

ACCENT = "#00b8d4"
MUTED = "grey58"
MAX_WIDTH = 96

# Custom geometric KDEV wordmark and a landscape terminal badge. Two pixel
# rows form one text line; the fifth line is reserved for command metadata.
# The compact badge uses its own four-line composition without that empty row.
_LOGO_ROWS = (
    " hhhhhhhhhhhhhh     hh    hh  ffffff    ffffffff  ff    ff",
    "hh            hh    hh   hh   fffffff   ffffffff  ff    ff",
    "hh  aa        hh    aa  aa    ff   fff  ff        ff    ff",
    "hh   aa       hh    aaaaa     ff    ff  ffffff     ff  ff ",
    "aa   aa       aa    ddddd     ff    ff  ffffff     ff  ff ",
    "aa  aa  dddd  aa    dd  dd    ff   fff  ff          ffff  ",
    "aa            aa    dd   dd   fffffff   ffffffff    ffff  ",
    " aaaaaaaaaaaaaa     dd    dd  ffffff    ffffffff     ff   ",
    "",
    "",
)
_LOGO_WIDTH = max(map(len, _LOGO_ROWS))
_ICON_WIDTH = 16
_ICON_HEIGHT = 8
_WORDMARK_LEFT = _ICON_WIDTH + 4
_LOGO_PALETTE = {
    "a": ACCENT,
    "h": "#43d5ed",
    "d": "#218eff",
    "f": "default",  # Wordmark follows the foreground of light and dark terminals.
}
_logo_printed: ContextVar[bool | None] = ContextVar("kdev_logo_printed", default=None)

THEME = Theme(
    {
        "kdev.accent": ACCENT,
        "kdev.muted": MUTED,
        "kdev.border": "grey42",
        "kdev.table_head": "bold grey58",
        "kdev.running": "bold green",
        "kdev.ok": "green",
        "kdev.warn": "yellow",
        "kdev.err": "red",
        "kdev.head": f"bold {ACCENT}",
        "kdev.cmd": f"bold {ACCENT}",
    }
)


def _no_colour() -> bool:
    """NO_COLOR is a standard, and piping into a file should stay readable."""
    return bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM") == "dumb"


console = Console(theme=THEME, no_color=_no_colour(), soft_wrap=False)
#: Failures go to stderr, so `kdev status --json | jq` never sees them.
errconsole = Console(theme=THEME, no_color=_no_colour(), soft_wrap=False, stderr=True)

_GLYPHS = {
    "ok": ("✓", "+"),
    "warn": ("!", "!"),
    "err": ("✗", "x"),
    "live": ("●", "*"),
    "pending": ("○", "-"),
    "bullet": ("·", "."),
    "full": ("█", "#"),
    "empty": ("░", "."),
    "prompt": ("›", ">"),
    "arrow": ("→", "->"),
}
_SPINNERS = ("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "|/-\\")


def _ascii_only() -> bool:
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return os.environ.get("TERM") == "dumb" or not (
        "utf" in encoding or encoding in ("", "cp65001")
    )


def g(name: str) -> str:
    """The right glyph for this terminal."""
    return _GLYPHS[name][1 if _ascii_only() else 0]


def _spinner_frames() -> str:
    return _SPINNERS[1 if _ascii_only() else 0]


STYLE = questionary.Style(
    [
        ("qmark", f"fg:{ACCENT} bold"),
        ("question", "bold"),
        ("answer", f"fg:{ACCENT} bold"),
        ("pointer", f"fg:{ACCENT} bold"),
        ("highlighted", f"fg:{ACCENT} bold"),
        ("selected", f"fg:{ACCENT}"),
        ("separator", "fg:#6c6c6c"),
        ("instruction", "fg:#6c6c6c"),
        ("disabled", "fg:#6c6c6c italic"),
    ]
)
# Questionary merges custom styles over its colored defaults, so an empty
# style is insufficient. Explicitly reset every token used by our prompts.
_MONO_STYLE = questionary.Style(
    [
        (token, "noinherit fg:default bg:default")
        for token in (
            "",
            "qmark",
            "question",
            "answer",
            "pointer",
            "highlighted",
            "selected",
            "separator",
            "instruction",
            "disabled",
            "text",
            "search_success",
            "search_none",
        )
    ]
)


def interactive() -> bool:
    """Only prompt when a human can answer; otherwise flags and defaults win."""
    return sys.stdin.isatty() and sys.stdout.isatty()


# --- lines --------------------------------------------------------------------

GUTTER = 2


def _line(target: Console, glyph: str, style: str, msg: str | Text) -> None:
    line = Text.assemble((f"{glyph:<{GUTTER}}", style), msg)
    target.print(Padding(line, (0, 0, 0, GUTTER), expand=False) if target.is_terminal else line)


def ok(msg: str | Text, hint_text: str = "") -> None:
    _line(console, g("ok"), "kdev.ok", msg)
    if hint_text:
        hint(hint_text)


def warn(msg: str | Text, hint_text: str = "") -> None:
    _line(console, g("warn"), "kdev.warn", msg)
    if hint_text:
        hint(hint_text)


def err(msg: str | Text, hint_text: str = "") -> None:
    _line(errconsole, g("err"), "kdev.err", msg)
    if hint_text:
        errconsole.print(
            Padding(Text(hint_text, style="kdev.muted"), (0, 0, 0, GUTTER), expand=False)
        )


def hint(msg: str | Text) -> None:
    # Padding rather than a literal prefix: a long hint that wraps keeps its
    # indent on the continuation lines instead of falling back to column 0.
    text = msg if isinstance(msg, Text) else Text(msg, style="kdev.muted")
    console.print(Padding(text, (0, 0, 0, GUTTER), expand=False))


def blank() -> None:
    console.print()


@contextmanager
def command_header_scope() -> Iterator[None]:
    """Show the logo once, including setup and actions picked from the overview."""
    token = _logo_printed.set(False)
    try:
        yield
    finally:
        _logo_printed.reset(token)


def _logo(*, compact: bool = False, metadata: Text | None = None) -> Text:
    art = Text(no_wrap=True, overflow="crop")
    width = _ICON_WIDTH if compact else _LOGO_WIDTH
    height = _ICON_HEIGHT if compact else len(_LOGO_ROWS)
    for y in range(0, height, 2):
        if y:
            art.append("\n")
        meta_row = not compact and metadata is not None and y == len(_LOGO_ROWS) - 2
        row_width = _WORDMARK_LEFT if meta_row else width
        top = _LOGO_ROWS[y][:row_width].ljust(row_width)
        bottom = _LOGO_ROWS[y + 1][:row_width].ljust(row_width)
        pixels: list[tuple[str, str | None]] = []
        for upper, lower in zip(top, bottom, strict=True):
            if upper == lower:
                pixels.append((" " if upper == " " else "█", _LOGO_PALETTE.get(upper)))
            elif lower == " ":
                pixels.append(("▀", _LOGO_PALETTE[upper]))
            elif upper == " ":
                pixels.append(("▄", _LOGO_PALETTE[lower]))
            else:
                pixels.append(("▀", f"{_LOGO_PALETTE[upper]} on {_LOGO_PALETTE[lower]}"))
        # Adjacent pixels share one style run, keeping ANSI output compact.
        for style, run in groupby(pixels, key=lambda pixel: pixel[1]):
            art.append("".join(char for char, _ in run), style)
        if meta_row and metadata is not None:
            art.append(metadata)
    return art


def header(
    title: str = "",
    context: Sequence[tuple[str, str]] = (),
    *,
    terminal_only: bool = False,
) -> None:
    """Responsive branding and context; terminal-only headers preserve piped data."""
    from . import __version__

    if terminal_only and not console.is_terminal:
        return
    metadata = Text.assemble(
        (title or "overview", "bold"),
        (f"  {g('bullet')}  v{__version__}", "kdev.muted"),
    )
    full_logo = console.width >= max(_LOGO_WIDTH, _WORDMARK_LEFT + metadata.cell_len) + GUTTER
    show_logo = (
        console.is_terminal
        and console.color_system
        and not console.no_color
        and not _no_colour()
        and not _ascii_only()
        and console.width >= _ICON_WIDTH + GUTTER
        and _logo_printed.get() is not True
    )
    if show_logo:
        console.print(
            Padding(
                _logo(compact=not full_logo, metadata=metadata), (0, 0, 0, GUTTER), expand=False
            )
        )
        if _logo_printed.get() is not None:
            _logo_printed.set(True)
    if not (show_logo and full_logo):
        if show_logo:
            console.print()
        line = Text.assemble(("kdev", "kdev.head"))
        if title:
            line.append(f" {g('bullet')} ", style="kdev.muted")
            line.append(title, style="bold")
        line.append(f"  v{__version__}", style="kdev.muted")
        if console.is_terminal:
            console.print(Padding(line, (0, 0, 0, GUTTER), expand=False))
        else:
            console.print(line)
    if context:
        if show_logo and full_logo:
            console.print()
        row = Text("  ")
        for i, (label, value) in enumerate(context):
            if i:
                row.append(f"  {g('bullet')}  ", style="kdev.muted")
            row.append(f"{label} ", style="kdev.muted")
            row.append(value)
        if row.cell_len <= console.width:
            console.print(row)
        else:
            # A hostname broken across two lines is unreadable and un-copyable.
            width = max(len(label) for label, _ in context)
            for label, value in context:
                console.print(
                    Text.assemble(("  ", ""), (f"{label:>{width}}  ", "kdev.muted"), value)
                )
    console.print()


def _facts_body(rows: Sequence[tuple[str, object]], width: int) -> RenderableType:
    """Keep long paths, hostnames and commands visible, even in a small window."""
    if width < 44:
        lines: list[Text] = []
        for label, value in rows:
            text = Text()
            if label:
                text.append(label + "\n", style="kdev.muted")
            text.append(value if isinstance(value, Text) else str(value))
            lines.append(text)
        return Group(*lines)
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="kdev.muted", justify="right", no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in rows:
        grid.add_row(label, value if isinstance(value, Text) else Text(str(value)))
    return grid


def facts(rows: Sequence[tuple[str, object]], indent: int = GUTTER) -> Padding:
    """Aligned facts, stacked when labels would crowd out their values."""
    width = min(console.width, MAX_WIDTH) - indent
    return Padding(_facts_body(rows, width), (0, 0, 0, indent), expand=False)


def section(title: str, detail: str = "") -> None:
    """A quiet heading for a group of related information in a terminal."""
    if not console.is_terminal:
        return
    text = Text(title, style="bold")
    if detail:
        text.append(f"  {g('bullet')}  {detail}", style="kdev.muted")
    console.print(Padding(text, (0, 0, 0, GUTTER), expand=False))
    console.print(
        Padding(
            Rule(style="kdev.border", characters="-" if _ascii_only() else "─"),
            (0, GUTTER),
        ),
        width=min(console.width, MAX_WIDTH),
    )


def card(title: str | Text, rows: Sequence[tuple[str, object]], tone: str = "muted") -> None:
    """A bounded result card, with space for facts and copyable commands."""
    terminal = console.is_terminal
    width = min(console.width, MAX_WIDTH)
    padding = (1, 2 if width >= 48 else 1)
    body = _facts_body(rows, width - padding[1] * 2 - 2)
    border = {"ok": "kdev.ok", "warn": "kdev.warn", "err": "kdev.err"}.get(
        tone, "kdev.border" if terminal else MUTED
    )
    console.print(
        Panel(
            body,
            title=Text(title, style="bold default") if isinstance(title, str) else title,
            title_align="left",
            border_style=border,
            box=box.ASCII if _ascii_only() else box.ROUNDED,
            padding=padding,
            width=width if terminal else None,
            expand=terminal,
        )
    )


class _ResponsiveTable(Table):
    """Retain Table's public API while giving narrow terminals labeled records."""

    def __rich_console__(self, target: Console, options: ConsoleOptions) -> RenderResult:
        width = min(options.max_width, MAX_WIDTH)
        threshold = 80 if len(self.columns) >= 5 else 64
        if target.is_terminal and width < threshold:
            if self.title:
                yield Padding(Text(str(self.title), style="bold"), (0, 0, 1, GUTTER))
            cells = [list(column.cells) for column in self.columns]
            for index in range(self.row_count):
                rows = [
                    (str(column.header).lower(), column_cells[index])
                    for column, column_cells in zip(self.columns, cells, strict=True)
                ]
                # An unlabeled leading column is a status marker; pair it with
                # the identity so an active account is still unambiguous.
                if rows and not rows[0][0] and len(rows) > 1:
                    marker, identity = rows[0][1], rows[1][1]
                    rows[1] = (
                        rows[1][0],
                        Text.assemble(
                            marker if isinstance(marker, Text) else str(marker),
                            " ",
                            identity if isinstance(identity, Text) else str(identity),
                        ),
                    )
                    rows.pop(0)
                yield Padding(_facts_body(rows, width - GUTTER), (0, GUTTER, 1, GUTTER))
            return
        yield from super().__rich_console__(target, options)


def table(
    columns: Sequence[tuple[str, str]], rows: Iterable[Sequence[object]], title: str = ""
) -> Table:
    """Readable tabular data; columns are (header, justify)."""
    # pad_edge puts the first column on the same two-space gutter as every
    # other line kdev prints.
    terminal = console.is_terminal
    t = _ResponsiveTable(
        box=(box.ASCII if _ascii_only() else box.SIMPLE_HEAD) if terminal else None,
        border_style="kdev.border",
        expand=False,
        padding=(0, 1) if terminal else (0, 2),
        header_style="kdev.table_head" if terminal else "kdev.muted",
        title=title or None,
        title_style="bold",
        title_justify="left",
        pad_edge=True,
        show_edge=not terminal,
        width=min(console.width, MAX_WIDTH) if terminal else None,
    )
    for name, justify in columns:
        t.add_column(name.upper(), justify=justify, overflow="fold")  # type: ignore[arg-type]
    for row in rows:
        t.add_row(*[c if isinstance(c, Text) else Text(str(c)) for c in row])
    return t


def next_steps(steps: Sequence[tuple[str, str]]) -> None:
    """What to run next. Commands in accent, aligned, one per line."""
    if not steps:
        return
    if console.is_terminal:
        section("Next")
        rows = [
            (Text(command, style="kdev.cmd"), Text(what, style="kdev.muted"))
            for command, what in steps
        ]
        if console.width < 64:
            for command, what in rows:
                console.print(Padding(Group(command, what), (0, GUTTER, 1, GUTTER)))
        else:
            grid = Table.grid(padding=(0, 3))
            grid.add_column(overflow="fold")
            grid.add_column(overflow="fold")
            for command, what in rows:
                grid.add_row(command, what)
            console.print(Padding(grid, (0, GUTTER)), width=min(console.width, MAX_WIDTH))
        return
    width = max(len(cmd) for cmd, _ in steps)
    for cmd, what in steps:
        hint(Text.assemble((f"{cmd:<{width}}", "kdev.cmd"), ("   " + what, "kdev.muted")))


def cmd(text: str) -> Text:
    """A command the user can type, styled as one."""
    return Text(text, style="kdev.cmd")


def live_dot(is_live: bool) -> Text:
    return Text(
        g("live") if is_live else g("pending"), style="kdev.ok" if is_live else "kdev.muted"
    )


class _Status(Protocol):
    """What `spinner` yields: rich's live status, or the no-TTY stand-in."""

    def update(self, *args: Any, **kwargs: Any) -> None: ...


@contextmanager
def spinner(text: str) -> Iterator[_Status]:
    """A transient spinner. Present tense, lowercase, ends in an ellipsis."""
    if not interactive():
        yield _NullStatus()
        return
    with console.status(
        Text(text, style="kdev.muted"),
        spinner="line" if _ascii_only() else "dots",
        spinner_style="kdev.accent",
        refresh_per_second=8,
    ) as status:
        yield status


class _NullStatus:
    def update(self, *_a, **_k) -> None:
        pass


def emit_json(data: object) -> None:
    """Machine-readable output: plain JSON on stdout, nothing else."""
    sys.stdout.write(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")


def bar(fraction: float, width: int = 14) -> Text:
    """A quota bar. Colour is the signal; the number beside it is the detail."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    colour = "green" if fraction > 0.5 else "yellow" if fraction > 0.15 else "red"
    t = Text(g("full") * filled, style=colour)
    t.append(g("empty") * (width - filled), style=MUTED)
    return t


def _fmt_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def fmt_hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f}h"


# --- accounts -----------------------------------------------------------------


@dataclass
class ProfileRow:
    name: str
    username: str
    gpu_left: int
    gpu_total: int
    tpu_left: int = 0
    tpu_total: int = 0
    active: bool = False
    error: str = ""

    @property
    def fraction(self) -> float:
        return self.gpu_left / self.gpu_total if self.gpu_total else 0.0

    @property
    def hours(self) -> str:
        return fmt_hours(self.gpu_left)


def accounts_table(rows: Sequence[ProfileRow]) -> Table:
    t = table(
        [
            ("", "left"),
            ("account", "left"),
            ("kaggle user", "left"),
            ("gpu this week", "left"),
            ("left", "right"),
            ("tpu", "right"),
        ],
        [],
    )
    t.columns[0].width = 1
    for r in rows:
        marker = Text(g("live") if r.active else " ", style="kdev.accent")
        name = Text(r.name, style="bold")
        if r.error:
            t.add_row(
                marker,
                name,
                Text(r.username, style="kdev.muted"),
                Text(r.error, style="kdev.err"),
                Text("-" if _ascii_only() else "—"),
                Text("-" if _ascii_only() else "—"),
            )
        else:
            meter = bar(r.fraction, width=10 if console.is_terminal else 14)
            if console.is_terminal:
                meter.append(f"  {r.fraction:.0%}", style="kdev.muted")
            t.add_row(
                marker,
                name,
                Text(r.username, style="kdev.muted"),
                meter,
                Text(r.hours, style="bold" if console.is_terminal else ""),
                Text(fmt_hours(r.tpu_left), style="kdev.muted"),
            )
    return t


def pick_profile(rows: Sequence[ProfileRow], need_hours: float) -> str:
    """Account picker with quota inline, most quota first."""
    usable = [r for r in rows if not r.error]
    if not usable:
        from .errors import KdevError

        raise KdevError(
            "Could not read quota for any account.", "Check they are signed in: kdev account"
        )
    usable.sort(key=lambda r: r.gpu_left, reverse=True)
    width = max(len(r.name) for r in usable)
    choices = []
    for r in usable:
        meter = "".join(g("full") if j < round(r.fraction * 10) else g("empty") for j in range(10))
        note = "" if r.gpu_left >= need_hours * 3600 else "   short for this session"
        choices.append(
            Choice(title=f"{r.name:<{width}}  {meter} {r.hours:>6} left{note}", value=r.name)
        )
    return select("Which account should run it?", choices)


# --- prompts ------------------------------------------------------------------


def _prompt_style() -> questionary.Style:
    return _MONO_STYLE if _no_colour() or console.no_color else STYLE


def select(question: str, choices: list[Choice]) -> str:
    answer = questionary.select(
        question,
        choices=choices,
        style=_prompt_style(),
        qmark=g("prompt"),
        pointer=g("prompt"),
        instruction="(up/down to move, enter to select)",
    ).ask()
    if answer is None:
        raise Cancelled()
    return answer


# Kept for callers that pass plain (value, label) pairs.
_select = select


def choose(question: str, options: Iterable[tuple[str, str]]) -> str:
    return select(question, [Choice(title=label, value=value) for value, label in options])


def confirm(question: str, default: bool = True) -> bool:
    """Without a terminal nobody can answer: take the default, which is "no"
    wherever saying yes would lose something."""
    if not interactive():
        return default
    answer = questionary.confirm(
        question, default=default, style=_prompt_style(), qmark=g("prompt")
    ).ask()
    if answer is None:
        raise Cancelled()
    return answer


def ask(question: str, default: str = "", secret: bool = False) -> str:
    # Called separately: `password` takes no default, and the two signatures
    # are honest on their own terms rather than joined through **kwargs.
    if secret:
        answer = questionary.password(question, style=_prompt_style(), qmark=g("prompt")).ask()
    else:
        answer = questionary.text(
            question, default=default, style=_prompt_style(), qmark=g("prompt")
        ).ask()
    if answer is None:
        raise Cancelled()
    return answer.strip()


GPU_CHOICES = [
    ("t4", "T4 ×2", "2× Tesla T4 · 29 GB RAM · the default"),
    ("p100", "P100", "1× Tesla P100 · 29 GB RAM · better fp64"),
    ("none", "CPU only", "4 cores · 30 GB RAM · uses no GPU quota"),
    ("tpu", "TPU v3-8", "96 cores · 330 GB RAM · 9h cap"),
]


def gpu_label(key: str) -> str:
    return {k: label for k, label, _ in GPU_CHOICES}.get(key, key)


def pick_gpu() -> str:
    return select(
        "Accelerator?",
        [Choice(title=f"{label:<10} {desc}", value=key) for key, label, desc in GPU_CHOICES],
    )


def pick_hours(gpu: str, quota_hours: float) -> float:
    cap = 9.0 if gpu == "tpu" else 12.0
    options = [h for h in (2.0, 4.0, 9.0, 12.0) if h <= cap]
    choices = []
    for h in options:
        note = f"   more than the {quota_hours:.1f}h left" if h > quota_hours else ""
        choices.append(Choice(title=f"{h:g} hours{note}", value=h))
    choices.append(Choice(title="Custom…", value=0.0))
    answer = float(select(f"Session length? (Kaggle stops it at {cap:g}h)", choices))
    if answer == 0.0:
        raw = questionary.text(
            "Hours:", default="6", style=_prompt_style(), qmark=g("prompt")
        ).ask()
        if raw is None:
            raise Cancelled()
        from .errors import KdevError

        try:
            answer = min(float(raw), cap)
        except ValueError as e:
            raise KdevError(f"{raw!r} is not a number of hours.") from e
        if answer <= 0:
            raise KdevError("Session length must be more than zero.")
    return answer


# --- the status board ---------------------------------------------------------


@dataclass
class _Stage:
    key: str
    label: str
    started: float = 0.0
    ended: float = 0.0
    failed: bool = False

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return (self.ended or time.monotonic()) - self.started


class Stages:
    """Live board, modelled on Docker Compose's: each step keeps its own line
    and clock, so a slow step is visibly slow rather than indistinguishable
    from a hung one. Plain appended lines when stdout is not a terminal."""

    def __init__(self, stages: Sequence[tuple[str, str]], head: str = ""):
        self._stages = [_Stage(k, text) for k, text in stages]
        self._by_key = {s.key: s for s in self._stages}
        self.head = head
        self.current: str | None = None
        self.detail = ""
        self.tick = 0
        self.started = time.monotonic()
        self._live: Live | None = None

    @property
    def done(self) -> list[str]:
        return [s.key for s in self._stages if s.ended and not s.failed]

    def __enter__(self) -> Stages:
        if interactive():
            self._live = Live(
                self._render(), console=console, refresh_per_second=8, transient=False
            )
            self._live.__enter__()
        elif self.head:
            console.print(Text("  " + self.head, style="kdev.muted"))
        return self

    def __exit__(self, *exc) -> None:
        if self._live:
            self._live.update(self._render(final=True))
            self._live.__exit__(*exc)

    def _marks(self, stage: _Stage, final: bool) -> tuple[Text, Text]:
        running = stage.key == self.current and not stage.ended and not final
        if stage.failed:
            glyph, style, label_style = g("err"), "kdev.err", "kdev.err"
        elif stage.ended or (stage.key == self.current and final):
            glyph, style, label_style = g("ok"), "kdev.ok", ""
        elif running:
            frames = _spinner_frames()
            glyph, style, label_style = frames[self.tick % len(frames)], "kdev.accent", "bold"
        else:
            glyph, style, label_style = g("pending"), "kdev.muted", "kdev.muted"
        return Text(glyph, style=style), Text(stage.label, style=label_style)

    def _render(self, final: bool = False) -> Group | Panel:
        self.tick += 1
        lines = []
        if self.head and not console.is_terminal:
            head = Text("  " + self.head, style="kdev.muted")
            head.append(f"   {_fmt_elapsed(time.monotonic() - self.started)}", style="kdev.muted")
            lines.append(head)
        board = Table.grid(padding=(0, 1), expand=console.is_terminal)
        board.add_column(width=2)
        board.add_column(width=1)
        board.add_column(ratio=1, overflow="fold")
        board.add_column(justify="right")
        for stage in self._stages:
            glyph, label = self._marks(stage, final)
            clock = _fmt_elapsed(stage.elapsed) if stage.started else ""
            board.add_row("", glyph, label, Text(clock, style="kdev.muted"))
            # A failed stage keeps its detail on the final frame: the reason it
            # failed is the whole point of the line.
            if self.detail and stage.key == self.current and (not final or stage.failed):
                board.add_row("", "", Text(self.detail, style="kdev.muted"), "")
        lines.append(board)
        if console.is_terminal:
            elapsed = _fmt_elapsed(time.monotonic() - self.started)
            completed = len(self.done)
            summary = Text(
                f"{completed}/{len(self._stages)} complete  {g('bullet')}  {elapsed}",
                style="kdev.muted",
            )
            content: list[RenderableType] = []
            if self.head:
                content.append(Text(self.head, style="bold"))
                content.append(Text(""))
            content.append(board)
            return Panel(
                Group(*content),
                title=Text("Progress", style="bold default"),
                subtitle=summary,
                title_align="left",
                subtitle_align="right",
                border_style="kdev.border",
                box=box.ASCII if _ascii_only() else box.ROUNDED,
                padding=(1, 1),
                width=min(console.width, MAX_WIDTH),
            )
        return Group(*lines)

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    def advance(self, key: str) -> None:
        now = time.monotonic()
        if self.current and self.current != key:
            previous = self._by_key.get(self.current)
            if previous and not previous.ended:
                previous.ended = now
        if key == self.current:
            return
        stage = self._by_key.get(key)
        if stage is None:
            return  # the remote side talking, not a step
        self.current = key
        stage.started = stage.started or now
        stage.ended = 0.0
        self.detail = ""
        if not self._live:
            console.print(f"  {g('prompt')} {stage.label}")
        else:
            self._refresh()

    def fail(self, message: str = "") -> None:
        stage = self._by_key.get(self.current or "")
        if stage:
            stage.failed = True
            stage.ended = stage.ended or time.monotonic()
        if message:
            self.detail = message[:96]
        if not self._live:
            err(message or "failed")
        else:
            self._refresh()

    def note(self, text: str) -> None:
        self.detail = text[:96]
        self._refresh()

    def finish(self) -> None:
        stage = self._by_key.get(self.current or "")
        if stage and not stage.ended:
            stage.ended = time.monotonic()
        if self._live:
            self._live.update(self._render(final=True))

    def refresh(self) -> None:
        self._refresh()


def ready_card(alias: str, host: str, hours: float, gpu: str, account: str, note: str = "") -> None:
    """How `up` ends: the facts about the box, and how to get in."""
    ends = time.localtime(time.time() + hours * 3600)
    rows: list[tuple[str, object]] = [
        ("host", Text(host, style="bold")),
        ("account", account),
        ("accelerator", gpu),
        ("ends", f"{time.strftime('%H:%M', ends)}  ({hours:g}h from now)"),
    ]
    if note:
        rows.append(("files", note))
    rows += [
        ("", ""),
        ("connect", cmd(f"ssh {alias}")),
        ("vs code", Text(f"Remote-SSH {g('arrow')} {alias}", style="kdev.muted")),
        ("stop", cmd("kdev down")),
    ]
    card(Text.assemble((g("ok") + " ", "kdev.ok"), ("ready", "kdev.ok")), rows, tone="ok")


def download(label: str, installer: Callable[..., Path]) -> Path:
    """Run an installer that reports (done, total) bytes, with a progress bar."""
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TransferSpeedColumn,
    )

    if not interactive():
        return installer(None)
    with Progress(
        SpinnerColumn("line" if _ascii_only() else "dots", style="kdev.accent"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(complete_style=ACCENT, finished_style="green"),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
        transient=True,
        refresh_per_second=8,
    ) as bars:
        task = bars.add_task(f"fetching {label}", total=None)

        def on_progress(done: int, total: int) -> None:
            bars.update(task, completed=done, total=total or None)

        return installer(on_progress)
