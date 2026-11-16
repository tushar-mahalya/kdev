"""kdev's design system. Every command builds its output from these parts only.

Not a full-screen app, on purpose: `kdev up` ends by handing you a shell, and a
UI you have to quit first would be in the way. The home screen (`kdev.menu`)
is an inline app that gives the terminal back the moment you pick. The
references, and what each contributed:

  Mole (tw93)     the front door: a keyed menu, and every action also a command
  clack           the rail that long, linear work is drawn on
  Docker Compose  a clock per step, so a slow step is visibly slow
  fly / vercel    the result card -- the few facts that matter, the command
                  to copy
  gh / Primer     glyph-and-gutter status lines; piped output as plain words
  Charm           a quiet palette where colour carries meaning, not decoration

Tokens
  accent  cyan    the brand, and commands you can run next
  ok      green   done, reachable, enough quota
  warn    yellow  needs attention, still works
  err     red     failed
  muted   grey    labels, secondary facts, hints
  bold            the values that matter: hosts, names, counts

Glyphs  ✓ done  ! attention  ✗ failed  ● live  ○ pending  › prompt  ◇ step done
        ┌ │ └ the rail  █ ▒ ░ a bar: left now, what the next box uses, used

Components
  header(title, ctx)     the logo on the front door and in setup; one line,
                         `›_ kdev › status`, everywhere else
  ok / warn / err        a status line; `hint` sits indented beneath; text in
                         `backticks` is drawn as a command
  facts(rows)            aligned label/value pairs
  card(title, rows)      the command's result, with facts and next actions
  section(title)         `Accounts ──────── 3 connected`, one line
  table(...)             restrained headers; labeled records when narrow;
                         tab-separated words when piped
  Stages                 the rail for long operations, with remembered timings
  bar / countdown        quota left (with the next box as a ghost), time left
  next_steps(cmds)       `→ kdev up   start the box`
  select / confirm / ask prompts; list rows are numbered and a digit picks
  spinner(text)          "doing something…", always present tense and lowercase

Voice
  Sentence case. No trailing full stop on a one-line status. Labels lowercase.
  A command, when shown, is the literal thing to type. Durations like 9h or
  2m04s, clock times like 14:05. One result card per command.

It degrades: without a TTY, boards become plain lines, cards plain facts and
prompts never appear; NO_COLOR is honoured; a terminal that cannot draw the
glyphs gets ASCII, and its typographic characters ASCII stand-ins.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
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
#: Set while the home menu runs a pick: it printed the header already.
_header_given: ContextVar[bool] = ContextVar("kdev_header_given", default=False)

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


def _pipe_width(stream) -> int | None:
    """Piped output is read by programs, not people: never wrap or crop it."""
    try:
        return None if stream.isatty() else 4096
    except (AttributeError, ValueError):
        return None


class _Console(Console):
    def on_broken_pipe(self) -> None:
        """`kdev logs | head`: the reader left early, which is not a failure.
        rich's own handler exits 1; scripts would read that as kdev failing."""
        self.quiet = True
        with suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(0)


console = _Console(
    theme=THEME, no_color=_no_colour(), soft_wrap=False, width=_pipe_width(sys.stdout)
)
#: Failures go to stderr, so `kdev status --json | jq` never sees them.
errconsole = _Console(
    theme=THEME, no_color=_no_colour(), soft_wrap=False, stderr=True, width=_pipe_width(sys.stderr)
)

_GLYPHS = {
    "ok": ("✓", "+"),
    "warn": ("!", "!"),
    "err": ("✗", "x"),
    "live": ("●", "*"),
    "pending": ("○", "-"),
    "bullet": ("·", "."),
    "full": ("█", "#"),
    "empty": ("░", "."),
    "ghost": ("▒", "="),
    "prompt": ("›", ">"),
    "done": ("◇", "o"),
    "rail": ("│", "|"),
    "top": ("┌", "+"),
    "bottom": ("└", "+"),
    "arrow": ("→", "->"),
}
_SPINNERS = ("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "|/-\\")


def _ascii_only() -> bool:
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return os.environ.get("TERM") == "dumb" or not (
        "utf" in encoding or encoding in ("", "cp65001")
    )


_ASCII = str.maketrans(
    {
        "·": ".",
        "—": "-",
        "–": "-",
        "…": "...",
        "×": "x",
        "→": "->",
        "›": ">",
        "‹": "<",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
    }
)


class _AsciiStream:
    """stdout for a terminal that cannot draw `×` or `·`: the few typographic
    characters in kdev's own sentences become their ASCII stand-ins, and
    anything else unencodable a `?`, never a UnicodeEncodeError."""

    def __init__(self, stream):
        self._stream = stream
        with suppress(AttributeError, ValueError):
            stream.reconfigure(errors="replace")

    def write(self, text: str) -> int:
        return self._stream.write(text.translate(_ASCII))

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


if _ascii_only() and not isinstance(sys.stdout, _AsciiStream):
    sys.stdout = _AsciiStream(sys.stdout)  # type: ignore[assignment]
    sys.stderr = _AsciiStream(sys.stderr)  # type: ignore[assignment]


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


def _styled(msg: str | Text, base: str = "", target: Console | None = None) -> Text:
    """`backticked` spans in kdev's sentences are commands: draw them as one.
    Where nothing is drawn (piped), the backticks stay: they are the quotes."""
    if isinstance(msg, Text):
        return msg
    if target is not None and not target.is_terminal:
        return Text(msg, style=base)
    text = Text(style=base)
    for i, part in enumerate(msg.split("`")):
        if part:
            text.append(part, style="kdev.cmd" if i % 2 else "")
    return text


def _line(target: Console, glyph: str, style: str, msg: str | Text) -> None:
    line = Text.assemble((f"{glyph:<{GUTTER}}", style), _styled(msg, target=target))
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
            Padding(_styled(hint_text, "kdev.muted", errconsole), (0, 0, 0, GUTTER), expand=False)
        )


def hint(msg: str | Text) -> None:
    """Advice for a person. Piped, it goes to stderr: stdout is the data
    (`kdev account | cut -f2`), and advice in it would be read as rows."""
    target = console if console.is_terminal else errconsole
    # Padding rather than a literal prefix: a long hint that wraps keeps its
    # indent on the continuation lines instead of falling back to column 0.
    target.print(Padding(_styled(msg, "kdev.muted", target), (0, 0, 0, GUTTER), expand=False))


def blank() -> None:
    """Breathing room for a person; piped data gets none (no empty rows)."""
    if console.is_terminal:
        console.print()


@contextmanager
def header_given() -> Iterator[None]:
    """Run a command whose header the caller already printed."""
    token = _header_given.set(True)
    try:
        yield
    finally:
        _header_given.reset(token)


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


#: Commands whose header is the full logo: the front door and first run.
#: Everything else gets one line, so the answer is on screen, not the brand.
_LOGO_TITLES = ("", "overview", "setup")


def _can_draw_logo() -> bool:
    return bool(
        console.is_terminal
        and console.color_system
        and not console.no_color
        and not _no_colour()
        and not _ascii_only()
        and console.width >= _ICON_WIDTH + GUTTER
        and _logo_printed.get() is not True
    )


def _mark() -> Text:
    """The one-line brand mark: the logo's own prompt and cursor, then the name."""
    if not console.is_terminal or _ascii_only() or console.no_color or _no_colour():
        return Text("kdev", style="kdev.head")
    return Text.assemble(("›_", "kdev.accent"), " ", ("kdev", "bold"))


def header(
    title: str = "",
    context: Sequence[tuple[str, str]] = (),
    *,
    terminal_only: bool = False,
    logo: bool | None = None,
) -> None:
    """Branding and context. The front door gets the logo; every other command
    one line -- `›_ kdev  status` with its context on the right -- so a
    command's answer is never pushed below the fold by its own banner.
    Terminal-only headers keep piped data clean."""
    from . import __version__

    if terminal_only and not console.is_terminal:
        return
    if _header_given.get():
        return
    width = min(console.width, MAX_WIDTH)
    want_logo = title in _LOGO_TITLES if logo is None else logo
    if want_logo and _can_draw_logo():
        metadata = Text.assemble(
            (title or "overview", "bold"),
            (f"  {g('bullet')}  v{__version__}", "kdev.muted"),
        )
        full = console.width >= max(_LOGO_WIDTH, _WORDMARK_LEFT + metadata.cell_len) + GUTTER
        console.print(
            Padding(_logo(compact=not full, metadata=metadata), (0, 0, 0, GUTTER), expand=False)
        )
        if not full:
            line = Text.assemble(_mark(), "  ", metadata)
            if line.cell_len + GUTTER > console.width:
                line = Text.assemble(_mark(), "  ", (title or "overview", "bold"))
            console.print()
            console.print(Padding(line, (0, 0, 0, GUTTER), expand=False))
        if _logo_printed.get() is not None:
            _logo_printed.set(True)
        if context:
            console.print()
            console.print(_context_line(context, width))
        console.print()
        return

    line = _mark()
    if title:
        line.append(f" {g('prompt')} ", style="kdev.muted")
        line.append(title, style="bold")
    if not context:
        line.append(f"  v{__version__}", style="kdev.muted")
    if not console.is_terminal:
        console.print(line)
        if context:
            console.print(_context_line(context, width))
        console.print()
        return
    right = _context_text(context)
    room = width - GUTTER * 2 - line.cell_len - 4
    if context and right.cell_len <= room:
        row = Table.grid(expand=True, padding=0)
        row.add_column(no_wrap=True)
        row.add_column(justify="right", no_wrap=True)
        row.add_row(line, right)
        console.print(Padding(row, (0, GUTTER)), width=width)
    else:
        console.print(Padding(line, (0, 0, 0, GUTTER), expand=False))
        if context:
            console.print(_context_line(context, width))
    console.print(
        Padding(Rule(style="kdev.border", characters="-" if _ascii_only() else "─"), (0, GUTTER)),
        width=width,
    )
    console.print()


def _context_text(context: Sequence[tuple[str, str]]) -> Text:
    row = Text()
    for i, (label, value) in enumerate(context):
        if i:
            row.append(f"  {g('bullet')}  ", style="kdev.muted")
        row.append(f"{label} ", style="kdev.muted")
        row.append(value)
    return row


def _context_line(context: Sequence[tuple[str, str]], width: int) -> RenderableType:
    row = _context_text(context)
    if row.cell_len + GUTTER <= width:
        return Padding(row, (0, 0, 0, GUTTER), expand=False)
    # A hostname broken across two lines is unreadable and un-copyable.
    label_w = max(len(label) for label, _ in context)
    return Group(
        *(
            Text.assemble((" " * GUTTER, ""), (f"{label:>{label_w}}  ", "kdev.muted"), value)
            for label, value in context
        )
    )


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


def facts(rows: Sequence[tuple[str, object]], indent: int = GUTTER) -> RenderableType:
    """Aligned facts, stacked when labels would crowd out their values.
    Piped: `label  value` lines, with no padding after the value."""
    if not console.is_terminal:
        label_w = max((len(label) for label, _ in rows), default=0)
        return Group(
            *(
                Text.assemble(
                    " " * indent + f"{label:>{label_w}}  ",
                    value if isinstance(value, Text) else str(value),
                    no_wrap=True,
                )
                for label, value in rows
                if label or value
            )
        )
    width = min(console.width, MAX_WIDTH) - indent
    return Padding(_facts_body(rows, width), (0, 0, 0, indent), expand=False)


def section(title: str, detail: str = "") -> None:
    """A quiet one-line heading: `Accounts ──────── 3 connected`."""
    if not console.is_terminal:
        return
    width = min(console.width, MAX_WIDTH) - GUTTER * 2
    rule = "-" if _ascii_only() else "─"
    head = Text(title, style="bold")
    tail = Text(detail, style="kdev.muted") if detail else Text()
    fill = width - head.cell_len - tail.cell_len - (4 if detail else 2)
    line = Text.assemble(head, " ", (rule * max(fill, 2), "kdev.border"))
    if detail:
        line.append(" ")
        line.append(tail)
    console.print(Padding(line, (0, 0, 0, GUTTER), expand=False), no_wrap=True, overflow="crop")


def card(title: str | Text, rows: Sequence[tuple[str, object]], tone: str = "muted") -> None:
    """A bounded result card, with space for facts and copyable commands.
    Piped, it is a title line and plain facts: boxes are for people."""
    terminal = console.is_terminal
    if not terminal:
        console.print(Text(title) if isinstance(title, str) else title)
        console.print(facts(rows))
        return
    # On the gutter, the width of the header's rule: one left edge for all.
    width = min(console.width, MAX_WIDTH) - GUTTER * 2
    padding = (1, 2 if width >= 48 else 1)
    body = _facts_body(rows, width - padding[1] * 2 - 2)
    border = {"ok": "kdev.ok", "warn": "kdev.warn", "err": "kdev.err"}.get(
        tone, "kdev.border" if terminal else MUTED
    )
    panel = Panel(
        body,
        title=Text(title, style="bold default") if isinstance(title, str) else title,
        title_align="left",
        border_style=border,
        box=box.ASCII if _ascii_only() else box.ROUNDED,
        padding=padding,
        width=width,
        expand=True,
    )
    console.print(Padding(panel, (0, 0, 0, GUTTER), expand=False))


class _ResponsiveTable(Table):
    """Retain Table's public API while giving narrow terminals labeled records.

    In a terminal the table sits on the same two-space gutter as every other
    line, header rule included."""

    _in_gutter = False

    def __rich_console__(self, target: Console, options: ConsoleOptions) -> RenderResult:
        width = min(options.max_width, MAX_WIDTH)
        if not target.is_terminal:
            # Piped: tab-separated words, one record a line, so `cut -f2`
            # and `awk -F'\t'` work and nothing wraps at 80 columns.
            # An unlabeled first column is the active-account marker: as data
            # it is a yes/no column called `active`.
            marker = bool(self.columns) and not str(self.columns[0].header).strip()
            header = [str(c.header).lower() or "-" for c in self.columns]
            if marker:
                header[0] = "active"
            cells = [list(c.cells) for c in self.columns]
            lines = ["\t".join(header)]
            for index in range(self.row_count):
                row = []
                for n, column in enumerate(cells):
                    cell = column[index]
                    plain = (cell.plain if isinstance(cell, Text) else str(cell)).strip()
                    if marker and n == 0:
                        plain = "yes" if plain else "no"
                    row.append("-" if plain in ("", "—") else plain)
                lines.append("\t".join(row))
            # Raw segments: rich expands tabs inside Text, which would turn
            # the columns back into spaces.
            from rich.segment import Segment

            for line in lines:
                yield Segment(line)
                yield Segment.line()
            return
        if target.is_terminal and not self._in_gutter and width >= self._narrow_at():
            self._in_gutter = True
            try:
                padded = Padding(self, (0, GUTTER))  # pyright: ignore[reportArgumentType]
                yield from target.render(padded, options.update(width=width))
            finally:
                self._in_gutter = False
            return
        if target.is_terminal and width < self._narrow_at():
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

    def _narrow_at(self) -> int:
        return 80 if len(self.columns) >= 5 else 64


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
        padding=(0, 2, 0, 0) if terminal else (0, 2),
        header_style="kdev.table_head" if terminal else "kdev.muted",
        title=title or None,
        title_style="bold",
        title_justify="left",
        pad_edge=not terminal,
        show_edge=not terminal,
        width=None,
    )
    for name, justify in columns:
        t.add_column(name.upper(), justify=justify, overflow="fold")  # type: ignore[arg-type]
    for row in rows:
        t.add_row(*[c if isinstance(c, Text) else Text(str(c)) for c in row])
    return t


def next_steps(steps: Sequence[tuple[str, str]]) -> None:
    """What to run next: `→ kdev up   start the box`, commands in accent."""
    if not steps:
        return
    if console.is_terminal:
        console.print()
        arrow = g("arrow")
        if console.width < 64:
            for command, what in steps:
                console.print(
                    Padding(
                        Group(
                            Text.assemble((arrow + " ", "kdev.muted"), (command, "kdev.cmd")),
                            Text("  " + " " * len(arrow) + what, style="kdev.muted"),
                        ),
                        (0, GUTTER),
                    )
                )
            return
        grid = Table.grid(padding=(0, 3))
        grid.add_column(no_wrap=True)
        grid.add_column(overflow="fold")
        for command, what in steps:
            grid.add_row(
                Text.assemble((arrow + " ", "kdev.muted"), (command, "kdev.cmd")),
                Text(what, style="kdev.muted"),
            )
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


class _SpinnerLine:
    """`⠋ checking…  4s`, on the gutter: the clock appears once a wait is
    long enough to wonder about, so slow never looks like stuck."""

    def __init__(self, text: str | Text):
        from rich.spinner import Spinner

        self.text: str | Text = text
        self.started = time.monotonic()
        self._spinner = Spinner("line" if _ascii_only() else "dots", style="kdev.accent")

    def update(self, text: str | Text | None = None, **_: Any) -> None:
        if text is not None:
            self.text = text

    def __rich__(self) -> RenderableType:
        label = self.text if isinstance(self.text, Text) else Text(self.text, style="kdev.muted")
        waited = time.monotonic() - self.started
        if waited >= 3:
            label = Text.assemble(label, (f"  {_fmt_elapsed(waited)}", "kdev.muted"))
        self._spinner.text = label
        return Padding(self._spinner, (0, 0, 0, GUTTER))


@contextmanager
def spinner(text: str) -> Iterator[_Status]:
    """A transient spinner. Present tense, lowercase, ends in an ellipsis."""
    if not interactive():
        yield _NullStatus()
        return
    line = _SpinnerLine(text)
    with Live(line, console=console, refresh_per_second=10, transient=True):
        yield line


class _NullStatus:
    def update(self, *_a, **_k) -> None:
        pass


def emit_json(data: object) -> None:
    """Machine-readable output: plain JSON on stdout, nothing else."""
    sys.stdout.write(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")


def _headroom_colour(fraction: float) -> str:
    return "green" if fraction > 0.5 else "yellow" if fraction > 0.15 else "red"


def bar(fraction: float, width: int = 14, ghost: float = 0.0) -> Text:
    """A quota bar. Colour is the signal; the number beside it is the detail.

    `ghost` is the share the next box would use, drawn as a lighter run at
    the end of the filled part: what is left now, and what will be after."""
    fraction = max(0.0, min(1.0, fraction))
    ghost = max(0.0, min(fraction, ghost))
    filled = round(fraction * width)
    after = round((fraction - ghost) * width) if ghost else filled
    after = min(after, filled - 1) if ghost and filled else after
    colour = _headroom_colour(fraction)
    t = Text(g("full") * max(after, 0), style=colour)
    if filled > after:
        t.append(g("ghost") * (filled - after), style=f"{colour} dim")
    t.append(g("empty") * (width - filled), style=MUTED)
    t.style = colour
    return t


def countdown(started: float, ends: float, width: int = 16, now: float | None = None) -> Text:
    """Time left on a box: a draining bar, then `5h12m left · ends 21:40`.
    Yellow under 30 minutes, red under 10 -- the moment to save work."""
    now = time.time() if now is None else now
    total = max(ends - started, 1.0)
    left = max(ends - now, 0.0)
    if not console.is_terminal:
        return Text(f"{fmt_left(left)} left, ends {time.strftime('%H:%M', time.localtime(ends))}")
    colour = "red" if left < 600 else "yellow" if left < 1800 else "green"
    filled = round(left / total * width)
    t = Text()
    t.append(g("full") * filled, style=colour)
    t.append(g("empty") * (width - filled), style=MUTED)
    t.append(f"  {fmt_left(left)} left", style="bold")
    t.append(f"  {g('bullet')}  ends {time.strftime('%H:%M', time.localtime(ends))}", "kdev.muted")
    return t


def fmt_when(at: float, now: float | None = None) -> str:
    """A moment in the coming week, as a person says it: `today 05:30`,
    `tomorrow 05:30`, `Sat 05:30 (in 4d)`."""
    now = time.time() if now is None else now
    local, today = time.localtime(at), time.localtime(now)
    clock = time.strftime("%H:%M", local)
    days = (
        time.mktime((*local[:3], 0, 0, 0, 0, 0, -1)) - time.mktime((*today[:3], 0, 0, 0, 0, 0, -1))
    ) / 86400
    days = round(days)
    if days <= 0:
        return f"today {clock}"
    if days == 1:
        return f"tomorrow {clock}"
    return f"{time.strftime('%a', local)} {clock} (in {days}d)"


def epoch(value: object) -> float:
    """Seconds since the epoch from a number or an ISO-8601 string; 0 if neither."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        from datetime import UTC, datetime

        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        return instant.timestamp()
    return 0.0


def fmt_left(seconds: float) -> str:
    """5h12m, 42m, 30s: what a person reads at a glance."""
    seconds = int(max(seconds, 0))
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


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
    #: When Kaggle refills this account's weekly quota (epoch seconds), if known.
    resets: float = 0.0

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
            if console.is_terminal:
                meter = Text.assemble(
                    bar(r.fraction, width=10), (f"  {r.fraction:.0%}", "kdev.muted")
                )
            else:
                meter = Text(f"{r.fraction:.0%}")
            t.add_row(
                marker,
                name,
                Text(r.username, style="kdev.muted"),
                meter,
                Text(r.hours, style="bold" if console.is_terminal else ""),
                Text(fmt_hours(r.tpu_left), style="kdev.muted"),
            )
    return t


def pick_profile(
    rows: Sequence[ProfileRow], need_hours: float, question: str = "Which account should run it?"
) -> str:
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
        if r.active:
            note += "   (active now)"
        choices.append(
            Choice(title=f"{r.name:<{width}}  {meter} {r.hours:>6} left{note}", value=r.name)
        )
    return select(question, choices)


# --- prompts ------------------------------------------------------------------


def _prompt_style() -> questionary.Style:
    return _MONO_STYLE if _no_colour() or console.no_color else STYLE


def _QMARK() -> str:
    """The prompt mark, on the gutter like every other line."""
    return " " * GUTTER + g("prompt")


def _answered(question: str, answer: str) -> None:
    """One answered line, the same for every kind of prompt and however it
    was answered: `  › Question  Answer`."""
    console.print(
        Padding(
            Text.assemble(
                (g("prompt") + " ", "kdev.accent"),
                (question, "bold"),
                "  ",
                (answer, "kdev.accent"),
            ),
            (0, 0, 0, GUTTER),
            expand=False,
        ),
        highlight=False,
    )


def _answer(question: Any) -> Any:
    """Ask, and turn Ctrl-C into None quietly: questionary's own `ask` prints
    "Cancelled by user", and kdev already says "cancelled" once."""
    unsafe = getattr(question, "unsafe_ask", None)
    if unsafe is None:
        return question.ask()
    try:
        return unsafe()
    except KeyboardInterrupt:
        return None


def select(question: str, choices: list[Choice]) -> str:
    """A list to pick from: arrows or j/k to move, enter to pick, or a digit
    to pick that row at once -- the numbers are shown beside each choice."""
    numbered: list[Choice] = []
    picks: dict[str, object] = {}
    labels: dict[int, str] = {}
    for choice in choices:
        if isinstance(choice, Choice) and not choice.disabled and len(picks) < 9:
            key = str(len(picks) + 1)
            picks[key] = choice.value
            title = choice.title if isinstance(choice.title, str) else str(choice.title)
            labels[id(choice.value)] = title.split("  ")[0].strip()
            numbered.append(
                Choice(
                    title=f"{key}  {title}",
                    value=choice.value,
                    disabled=choice.disabled,
                    checked=choice.checked,
                    description=getattr(choice, "description", None),
                )
            )
        else:
            numbered.append(choice)
    question_ = questionary.select(
        question,
        choices=numbered,
        style=_prompt_style(),
        qmark=_QMARK(),
        pointer=" " + g("prompt"),
        instruction=" ",
        use_jk_keys=True,
        use_indicator=False,
        erase_when_done=True,
    )
    app = getattr(question_, "application", None)
    if app is not None and picks:
        from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings

        extra = KeyBindings()
        for key, value in picks.items():

            @extra.add(key, eager=True)
            def _pick(event, value=value) -> None:
                event.app.exit(result=value)

        app.key_bindings = merge_key_bindings([app.key_bindings, extra])  # type: ignore[list-item]
    answer = _answer(question_)
    if answer is None:
        raise Cancelled()
    # One answered line, the same however it was picked: `› Question  Answer`.
    label = labels.get(id(answer)) or next(
        (
            lb
            for v, lb in (
                (c.value, labels.get(id(c.value))) for c in choices if isinstance(c, Choice)
            )
            if v == answer and lb
        ),
        str(answer),
    )
    _answered(question, label)
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
    answer = _answer(
        questionary.confirm(
            question,
            default=default,
            style=_prompt_style(),
            qmark=_QMARK(),
            erase_when_done=True,
        )
    )
    if answer is None:
        raise Cancelled()
    _answered(question, "yes" if answer else "no")
    return answer


def ask(question: str, default: str = "", secret: bool = False) -> str:
    # Called separately: `password` takes no default, and the two signatures
    # are honest on their own terms rather than joined through **kwargs.
    common = {"style": _prompt_style(), "qmark": _QMARK(), "erase_when_done": True}
    if secret:
        answer = _answer(questionary.password(question, **common))
    else:
        answer = _answer(questionary.text(question, default=default, **common))
    if answer is None:
        raise Cancelled()
    answer = answer.strip()
    _answered(question, "•" * min(len(answer), 8) if secret else answer or "—")
    return answer


GPU_CHOICES = [
    ("t4", "T4 ×2", "2× Tesla T4 · 29 GB RAM · the default"),
    ("p100", "P100", "1× Tesla P100 · 29 GB RAM · better fp64"),
    ("none", "CPU only", "4 cores · 30 GB RAM · uses no GPU quota"),
    ("tpu", "TPU v3-8", "96 cores · 330 GB RAM · 9h cap"),
]


def gpu_label(key: str) -> str:
    label = {k: label for k, label, _ in GPU_CHOICES}.get(key, key)
    return label.replace("×", "x") if _ascii_only() else label


def pick_gpu() -> str:
    return select(
        "Accelerator?",
        [Choice(title=f"{label:<10} {desc}", value=key) for key, label, desc in GPU_CHOICES],
    )


def pick_hours(gpu: str, quota_hours: float | None = None) -> float:
    """Session length, each choice with what it leaves of this week's quota
    (`quota_hours`, when known; a CPU box uses none)."""
    cap = 9.0 if gpu == "tpu" else 12.0
    options = [h for h in (2.0, 4.0, 9.0, 12.0) if h <= cap]
    choices = []
    for h in options:
        note = ""
        if quota_hours is not None and gpu != "none":
            if h > quota_hours:
                note = f"   more than the {quota_hours:.1f}h left; Kaggle stops it early"
            else:
                note = f"   leaves {quota_hours - h:.1f}h this week"
        choices.append(Choice(title=f"{h:g} hours".ljust(8) + note, value=h))
    choices.append(Choice(title="Custom…", value=0.0))
    answer = float(select(f"Session length? (Kaggle stops it at {cap:g}h)", choices))
    if answer == 0.0:
        raw = _answer(
            questionary.text(
                "Hours", default="6", style=_prompt_style(), qmark=_QMARK(), erase_when_done=True
            )
        )
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


def _timings_path() -> Path:
    from .config import CONFIG_DIR

    return CONFIG_DIR / "timings.json"


def _load_timings() -> dict[str, float]:
    try:
        data = json.loads(_timings_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def _save_timings(measured: dict[str, float], previous: dict[str, float]) -> None:
    """Remember each step's time, smoothed, so a slow step reads as slow."""
    if not measured:
        return
    merged = dict(previous)
    for key, seconds in measured.items():
        old = previous.get(key)
        merged[key] = round(seconds if old is None else old * 0.6 + seconds * 0.4, 1)
    try:
        path = _timings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, indent=1, sort_keys=True))
    except OSError:
        pass


class _LiveBoard:
    """What Live draws: the board as it is at this refresh, not as it was."""

    def __init__(self, board: Stages):
        self.board = board

    def __rich__(self) -> RenderableType:
        return self.board._render(final=self.board._final)


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
        #: How long each step took last time on this machine, by key.
        self.usual: dict[str, float] = _load_timings()
        self._final = False
        #: Whether this run's clocks are worth remembering. A joined session's
        #: log replays in milliseconds and would make "usually" meaningless.
        self.record = True

    @property
    def done(self) -> list[str]:
        return [s.key for s in self._stages if s.ended and not s.failed]

    def __enter__(self) -> Stages:
        if interactive():
            # The board renders itself on every refresh, so its clocks and
            # spinner keep moving through a long, silent step.
            self._live = Live(
                _LiveBoard(self), console=console, refresh_per_second=8, transient=False
            )
            self._live.__enter__()
        elif self.head:
            console.print(Text("  " + self.head, style="kdev.muted"))
        return self

    def __exit__(self, *exc) -> None:
        if exc[0] is not None:
            # Interrupted or failed mid-step: that step did not finish, and
            # the last frame must not draw it as done.
            stage = self._by_key.get(self.current or "")
            if stage and not stage.ended:
                stage.failed = True
                stage.ended = time.monotonic()
        if self._live:
            self._final = True
            self._live.update(_LiveBoard(self))
            self._live.__exit__(*exc)
        complete = len(self.done) == len(self._stages)
        if self._live and exc[0] is None and self.record and complete:
            _save_timings(
                {s.key: s.elapsed for s in self._stages if s.ended and not s.failed}, self.usual
            )

    def _marks(self, stage: _Stage, final: bool) -> tuple[Text, Text]:
        running = stage.key == self.current and not stage.ended and not final
        if stage.failed:
            glyph, style, label_style = g("err"), "kdev.err", "kdev.err"
        elif stage.ended or (stage.key == self.current and final):
            glyph, style, label_style = g("done"), "kdev.ok", ""
        elif running:
            frames = _spinner_frames()
            glyph, style, label_style = frames[self.tick % len(frames)], "kdev.accent", "bold"
        else:
            glyph, style, label_style = g("pending"), "kdev.muted", "kdev.muted"
        return Text(glyph, style=style), Text(stage.label, style=label_style)

    def _render(self, final: bool = False) -> Group:
        """A rail, after clack: one row per step, each with its own clock, the
        running one with how long it usually takes on this machine."""
        self.tick += 1
        rail = Text(g("rail"), style="kdev.border")
        board = Table.grid(padding=(0, 2), expand=False)
        board.add_column(width=1, no_wrap=True)
        board.add_column(overflow="fold", min_width=min(36, max(console.width - 24, 10)))
        board.add_column(justify="right", no_wrap=True, min_width=5)
        board.add_column(no_wrap=True, style="kdev.muted")
        failed = next((s for s in self._stages if s.failed), None)
        lines: list[RenderableType] = []
        if self.head:
            lines.append(
                Padding(
                    Text.assemble((g("top") + "  ", "kdev.border"), (self.head, "bold")),
                    (0, 0, 0, GUTTER),
                )
            )
            board.add_row(rail, "", "", "")
        for stage in self._stages:
            glyph, label = self._marks(stage, final)
            clock = Text(_fmt_elapsed(stage.elapsed) if stage.started else "", "kdev.muted")
            running = stage.key == self.current and not stage.ended and not final
            usual = self.usual.get(stage.key)
            # Only once it is worth knowing, and never for a step that is
            # usually instant: "usually 0s" would only alarm.
            note = (
                f"usually {_fmt_elapsed(usual)}"
                if running and usual and usual >= 3 and stage.elapsed >= 2
                else ""
            )
            board.add_row(glyph, label, clock, note)
            # A failed stage keeps its detail on the final frame: the reason it
            # failed is the whole point of the line.
            if self.detail and stage.key == self.current and (not final or stage.failed):
                board.add_row(rail, Text(self.detail, style="kdev.muted"), "", "")
        board.add_row(rail, "", "", "")
        elapsed = _fmt_elapsed(time.monotonic() - self.started)
        if failed:
            end = Text.assemble(
                (g("bottom") + "  ", "kdev.err"),
                (f"stopped at {failed.label.lower()}", "kdev.err"),
                (f"  {g('bullet')}  {elapsed}", "kdev.muted"),
            )
        elif final and len(self.done) == len(self._stages):
            end = Text.assemble(
                (g("bottom") + "  ", "kdev.ok"),
                ("done", "kdev.ok"),
                (f" in {elapsed}", "kdev.muted"),
            )
        else:
            end = Text.assemble(
                (g("bottom") + "  ", "kdev.border"),
                (
                    f"{len(self.done)}/{len(self._stages)} complete  {g('bullet')}  {elapsed}",
                    "kdev.muted",
                ),
            )
        lines.append(Padding(board, (0, 0, 0, GUTTER)))
        lines.append(Padding(end, (0, 0, 0, GUTTER)))
        return Group(*lines)

    def _refresh(self) -> None:
        if self._live:
            self._live.refresh()

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
            self._final = True
            self._live.refresh()

    def refresh(self) -> None:
        self._refresh()


def ready_card(
    alias: str,
    host: str,
    hours: float,
    gpu: str,
    account: str,
    note: str = "",
    *,
    started: float = 0.0,
    ends: float = 0.0,
) -> None:
    """How `up` ends: the facts about the box, and how to get in. `started`
    and `ends` are the box's own record when known: it stops a little before
    Kaggle would, so the clock must come from it, not from `hours`."""
    now = time.time()
    if ends:
        hours = max(ends - now, 0) / 3600
    rows: list[tuple[str, object]] = [
        ("host", Text(host, style="bold")),
        ("account", account),
        ("accelerator", gpu),
        (
            "time",
            countdown(now, now + hours * 3600, width=12)
            if console.is_terminal
            else f"{hours:g}h, ends {time.strftime('%H:%M', time.localtime(now + hours * 3600))}",
        ),
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
