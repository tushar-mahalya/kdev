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
  header(title, facts)   one line: what this command is acting on
  ok / warn / err        a status line; `hint` sits indented beneath
  facts(rows)            aligned label/value pairs
  card(title, rows)      the one bordered block a command may print: its result
  table(...)             borderless, muted uppercase headers
  Stages                 the live board for long operations
  next_steps(cmds)       what to run next, commands in accent
  spinner(text)          "doing something…", always present tense and lowercase

Voice
  Sentence case. No trailing full stop on a one-line status. Labels lowercase.
  A command, when shown, is the literal thing to type. Durations like 9h or
  2m04s, clock times like 14:05. One card per command at most.

It degrades: without a TTY, boards become plain lines and prompts never
appear; NO_COLOR is honoured; a terminal that cannot draw the glyphs gets ASCII.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import questionary
from questionary import Choice
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .errors import Cancelled

ACCENT = "#00b8d4"
MUTED = "grey58"

THEME = Theme(
    {
        "kdev.accent": ACCENT,
        "kdev.muted": MUTED,
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
    return not ("utf" in encoding or encoding in ("", "cp65001"))


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


def interactive() -> bool:
    """Only prompt when a human can answer; otherwise flags and defaults win."""
    return sys.stdin.isatty() and sys.stdout.isatty()


# --- lines --------------------------------------------------------------------

GUTTER = 2


def _line(target: Console, glyph: str, style: str, msg: str | Text) -> None:
    target.print(Text.assemble((f"{glyph:<{GUTTER}}", style), msg))


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


def header(title: str = "", context: Sequence[tuple[str, str]] = ()) -> None:
    """What this command is acting on, in one line (two on a narrow terminal)."""
    from . import __version__

    line = Text.assemble(("kdev", "kdev.head"))
    if title:
        line.append(f" {g('bullet')} ", style="kdev.muted")
        line.append(title, style="bold")
    line.append(f"  v{__version__}", style="kdev.muted")
    console.print(line)
    if context:
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


def facts(rows: Sequence[tuple[str, object]], indent: int = GUTTER) -> Table:
    """Aligned label/value pairs. Labels muted and right-aligned."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="kdev.muted", justify="right")
    grid.add_column()
    for label, value in rows:
        grid.add_row(label, value if isinstance(value, Text) else Text(str(value)))
    return Padding(grid, (0, 0, 0, indent), expand=False)  # type: ignore[return-value]


def card(title: str | Text, rows: Sequence[tuple[str, object]], tone: str = "muted") -> None:
    """The one bordered block a command may print: its result."""
    body = Table.grid(padding=(0, 2))
    body.add_column(style="kdev.muted", justify="right")
    body.add_column()
    for label, value in rows:
        body.add_row(label, value if isinstance(value, Text) else Text(str(value)))
    border = {"ok": "kdev.ok", "warn": "kdev.warn", "err": "kdev.err"}.get(tone, MUTED)
    console.print(
        Panel(
            body, title=title, title_align="left", border_style=border, padding=(1, 2), expand=False
        )
    )


def table(
    columns: Sequence[tuple[str, str]], rows: Iterable[Sequence[object]], title: str = ""
) -> Table:
    """Borderless; columns are (header, justify)."""
    # pad_edge puts the first column on the same two-space gutter as every
    # other line kdev prints.
    t = Table(
        box=None,
        expand=False,
        padding=(0, 2),
        header_style="kdev.muted",
        title=title or None,
        title_style="bold",
        title_justify="left",
        pad_edge=True,
    )
    for name, justify in columns:
        t.add_column(name.upper(), justify=justify)  # type: ignore[arg-type]
    for row in rows:
        t.add_row(*[c if isinstance(c, Text) else Text(str(c)) for c in row])
    return t


def next_steps(steps: Sequence[tuple[str, str]]) -> None:
    """What to run next. Commands in accent, aligned, one per line."""
    if not steps:
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


@contextmanager
def spinner(text: str) -> Iterator[object]:
    """A transient spinner. Present tense, lowercase, ends in an ellipsis."""
    if not interactive():
        yield _NullStatus()
        return
    with console.status(Text(text, style="kdev.muted"), spinner="dots") as status:
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
                Text("—"),
                Text("—"),
            )
        else:
            t.add_row(
                marker,
                name,
                Text(r.username, style="kdev.muted"),
                bar(r.fraction),
                Text(r.hours),
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


def select(question: str, choices: list[Choice]) -> str:
    answer = questionary.select(
        question, choices=choices, style=STYLE, qmark=g("prompt"), instruction=" "
    ).ask()
    if answer is None:
        raise Cancelled()
    return answer


# Kept for callers that pass plain (value, label) pairs.
_select = select


def choose(question: str, options: Iterable[tuple[str, str]]) -> str:
    return select(question, [Choice(title=label, value=value) for value, label in options])


def confirm(question: str, default: bool = True) -> bool:
    answer = questionary.confirm(question, default=default, style=STYLE, qmark=g("prompt")).ask()
    if answer is None:
        raise Cancelled()
    return answer


def ask(question: str, default: str = "", secret: bool = False) -> str:
    fn = questionary.password if secret else questionary.text
    kwargs = {} if secret else {"default": default}
    answer = fn(question, style=STYLE, qmark=g("prompt"), **kwargs).ask()
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
    answer = select(f"Session length? (Kaggle stops it at {cap:g}h)", choices)
    if answer == 0.0:
        raw = questionary.text("Hours:", default="6", style=STYLE, qmark=g("prompt")).ask()
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
                self._render(), console=console, refresh_per_second=12, transient=False
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

    def _render(self, final: bool = False) -> Group:
        self.tick += 1
        lines = []
        if self.head:
            head = Text("  " + self.head, style="kdev.muted")
            head.append(f"   {_fmt_elapsed(time.monotonic() - self.started)}", style="kdev.muted")
            lines.append(head)
        board = Table.grid(padding=(0, 1))
        board.add_column(width=2)
        board.add_column(width=1)
        board.add_column(ratio=1)
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


def download(label: str, installer) -> object:
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
        SpinnerColumn(style="kdev.accent"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(complete_style=ACCENT, finished_style="green"),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
        transient=True,
    ) as bars:
        task = bars.add_task(f"fetching {label}", total=None)

        def on_progress(done: int, total: int) -> None:
            bars.update(task, completed=done, total=total or None)

        return installer(on_progress)
