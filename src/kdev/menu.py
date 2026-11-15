"""The home screen's live menu: a small inline app, not a full-screen one.

It draws below whatever kdev already printed, so the scrollback stays a
readable record, and it gives the terminal back the moment you pick
something. What it adds over a plain list:

  * it is usable at once -- the box and quota fill in while you look, each
    with its own spinner, instead of a blank wait before anything appears;
  * every item has a key, and arrows, j/k and enter work too;
  * the highlighted item shows the exact command it runs, so the menu
    teaches the scriptable form as you use it;
  * a running box counts down in place.

Only ever entered from `ui.interactive()`; without a terminal the home
screen prints plain lines instead.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

from . import ui

Fragments = StyleAndTextTuples


@dataclass
class Item:
    """One thing the menu can do."""

    key: str
    label: str
    argv: Sequence[str] = ()
    #: Shown beside the label: why it is the way it is ("T4 ×2 · 9h").
    note: str = ""
    #: Why it cannot be picked right now; empty means it can.
    disabled: str = ""
    group: str = ""

    @property
    def command(self) -> str:
        return "kdev " + " ".join(self.argv) if self.argv else ""


@dataclass
class Live:
    """Facts that arrive while the menu is already on screen.

    `slots` maps a name to its value once loaded; a missing name is still
    loading. Loaders run on threads and only ever assign whole values, so a
    render never sees half of one.
    """

    slots: dict[str, object] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    changed: Callable[[], None] = lambda: None

    def load(self, name: str, fn: Callable[[], object]) -> None:
        def run() -> None:
            try:
                self.slots[name] = fn()
            except Exception as e:  # shown in place; never kills the menu
                self.errors[name] = str(e) or type(e).__name__
                self.slots[name] = None
            self.changed()

        threading.Thread(target=run, name=f"kdev-load-{name}", daemon=True).start()

    def ready(self, name: str) -> bool:
        return name in self.slots

    def get(self, name: str, default: object = None) -> object:
        return self.slots.get(name, default)


STYLE = Style.from_dict(
    {
        "accent": f"fg:{ui.ACCENT}",
        "accent.bold": f"fg:{ui.ACCENT} bold",
        "muted": "fg:#8a8a8a",
        "faint": "fg:#5f5f5f",
        "ok": "fg:ansigreen",
        "warn": "fg:ansiyellow",
        "err": "fg:ansired",
        "bold": "bold",
        "key": f"fg:{ui.ACCENT} bold",
        "key.off": "fg:#5f5f5f",
        "pointer": f"fg:{ui.ACCENT} bold",
        "label.on": "bold",
        "label.off": "fg:#6c6c6c",
        "rail": "fg:#4e4e4e",
        "cmd": f"fg:{ui.ACCENT}",
    }
)
MONO = Style.from_dict({})


def spinner_frame() -> str:
    frames = ui._spinner_frames()
    return frames[int(time.monotonic() * 10) % len(frames)]


#: Terminal heights (rows) at which the menu folds its secondary parts.
SHORT = 20
TINY = 15


class Menu:
    """Pick one item; returns it, or None for quit.

    `dashboard()` is redrawn ten times a second, so it can show spinners and
    countdowns; it must be cheap and must not block.
    """

    def __init__(
        self,
        items: Callable[[], Sequence[Item]],
        dashboard: Callable[[int], Fragments],
        live: Live | None = None,
        title: str = "What now?",
        *,
        input=None,  # prompt_toolkit input and output, for tests
        output=None,
    ):
        self._items = items
        self._dashboard = dashboard
        self.live = live or Live()
        self.title = title
        self.index = 0
        self.flash = ""
        self._app: Application | None = None
        self._io = {"input": input, "output": output}

    # --- state ----------------------------------------------------------------

    def _enabled(self) -> list[int]:
        return [i for i, item in enumerate(self._items()) if not item.disabled]

    def _move(self, step: int) -> None:
        items = self._items()
        if not items:
            return
        for _ in range(len(items)):
            self.index = (self.index + step) % len(items)
            if not items[self.index].disabled:
                break
        self.flash = ""

    def _settle(self) -> None:
        """Keep the pointer on something pickable as items change under it."""
        items = self._items()
        if not items:
            self.index = 0
            return
        self.index = min(self.index, len(items) - 1)
        if items[self.index].disabled:
            enabled = self._enabled()
            if enabled:
                self.index = min(enabled, key=lambda i: abs(i - self.index))

    # --- drawing --------------------------------------------------------------

    def _height(self) -> int:
        try:
            return self._app.output.get_size().rows if self._app else 24
        except Exception:
            return 24

    def _width(self) -> int:
        try:
            return min(self._app.output.get_size().columns, ui.MAX_WIDTH) if self._app else 80
        except Exception:
            return 80

    def _render_menu(self) -> Fragments:
        """Main items one per line, numbered; the rest as a row of keyed chips."""
        self._settle()
        width = self._width()
        items = self._items()
        out: Fragments = []
        pad = " " * ui.GUTTER
        out += [("", "\n"), ("", pad), ("class:bold", self.title), ("", "\n")]
        main = [(n, i) for n, i in enumerate(items) if not i.group]
        more = [(n, i) for n, i in enumerate(items) if i.group]
        label_w = max((len(i.label) for _, i in main), default=0)
        show_notes = width >= ui.GUTTER + 6 + label_w + 16
        for n, item in main:
            on, off = n == self.index, bool(item.disabled)
            out.append(("", pad))
            out.append(("class:pointer", ui.g("prompt") + " ") if on else ("", "  "))
            out.append(("class:key.off" if off else "class:key", item.key))
            out.append(("", "  "))
            label_style = "class:label.off" if off else "class:label.on" if on else ""
            out.append((label_style, item.label.ljust(label_w)))
            note = item.disabled or item.note
            if note and show_notes:
                room = width - ui.GUTTER - 6 - label_w - 3
                if len(note) > room:
                    note = note[: max(room - 1, 0)] + ("…" if room > 1 else "")
                out.append(("class:faint" if off else "class:muted", "   " + note))
            out.append(("", "\n"))
        # A short terminal (an editor's panel) keeps every key on one line:
        # the label of the chip under the pointer, the others as bare keys.
        compact = self._height() < SHORT
        if more:
            out.append(("", "\n"))
            line_len = ui.GUTTER
            out.append(("", pad))
            if compact:
                out.append(("class:faint", "more "))
            for n, item in more:
                on = n == self.index
                if compact:
                    out.append(("class:pointer", ui.g("prompt")) if on else ("", " "))
                    out.append(("class:key", item.key))
                    if on:
                        out.append(("class:label.on", " " + item.label))
                    continue
                chip = 2 + len(item.key) + 1 + len(item.label) + 1
                if line_len + chip > width and line_len > ui.GUTTER:
                    out += [("", "\n"), ("", pad)]
                    line_len = ui.GUTTER
                out.append(("class:pointer", ui.g("prompt") + " ") if on else ("", "  "))
                out.append(("class:key", item.key))
                out.append(("class:label.on" if on else "class:muted", " " + item.label + " "))
                line_len += chip
        return out

    def _render_footer(self) -> Fragments:
        items = self._items()
        current = items[self.index] if items else None
        pad = " " * ui.GUTTER
        out: Fragments = [("", "\n")]
        if self.flash:
            out += [("", pad), ("class:warn", self.flash), ("", "\n")]
        elif current and current.command:
            out += [
                ("", pad),
                ("class:faint", "$ "),
                ("class:cmd", current.command),
                ("", "\n"),
            ]
        else:
            out += [("", "\n")]
        if self._height() < TINY:
            return out[:-1] if out and out[-1] == ("", "\n") else out
        sep = f"  {ui.g('bullet')}  "
        keys = [
            ("↑↓" if not ui._ascii_only() else "up/down", "move"),
            ("enter", "run"),
            ("1-9 a-z", "pick"),
            ("q", "quit"),
        ]
        out.append(("", pad))
        for i, (key, what) in enumerate(keys):
            if i:
                out.append(("class:faint", sep))
            out += [("class:muted bold", key), ("class:faint", " " + what)]
        return out

    # --- running --------------------------------------------------------------

    def run(self) -> Item | None:
        kb = KeyBindings()
        result: dict[str, Item | None] = {"item": None}

        def pick(event: KeyPressEvent, item: Item) -> None:
            if item.disabled:
                self.flash = f"{item.label}: {item.disabled}"
                return
            result["item"] = item
            event.app.exit()

        @kb.add("down")
        @kb.add("j")
        @kb.add("tab")
        def _down(event: KeyPressEvent) -> None:
            self._move(1)

        @kb.add("up")
        @kb.add("k")
        @kb.add("s-tab")
        def _up(event: KeyPressEvent) -> None:
            self._move(-1)

        @kb.add("enter")
        @kb.add("right")
        def _enter(event: KeyPressEvent) -> None:
            items = self._items()
            if items:
                pick(event, items[self.index])

        @kb.add("q")
        @kb.add("escape", eager=True)
        @kb.add("c-c")
        @kb.add("c-d")
        def _quit(event: KeyPressEvent) -> None:
            result["item"] = None
            event.app.exit()

        @kb.add("<any>")
        def _key(event: KeyPressEvent) -> None:
            pressed = event.data.lower()
            for n, item in enumerate(self._items()):
                if item.key == pressed:
                    self.index = n
                    pick(event, item)
                    return

        def plain(render: Callable[[], Fragments]) -> Callable[[], Fragments]:
            if not ui._ascii_only():
                return render

            def translated() -> Fragments:
                return [(f[0], f[1].translate(ui._ASCII)) for f in render()]

            return translated

        body = HSplit(
            [
                Window(
                    FormattedTextControl(plain(lambda: self._dashboard(self._width()))),
                    dont_extend_height=True,
                    wrap_lines=False,
                ),
                Window(
                    FormattedTextControl(plain(self._render_menu)),
                    dont_extend_height=True,
                    wrap_lines=False,
                ),
                Window(
                    FormattedTextControl(plain(self._render_footer)),
                    dont_extend_height=True,
                    wrap_lines=False,
                ),
            ]
        )
        style = MONO if ui._no_colour() or ui.console.no_color else STYLE
        self._app = Application(
            layout=Layout(body),
            key_bindings=kb,
            style=style,
            full_screen=False,
            erase_when_done=True,
            refresh_interval=0.1,
            mouse_support=False,
            include_default_pygments_style=False,
            **{k: v for k, v in self._io.items() if v is not None},
        )
        app = self._app

        def changed() -> None:
            # Called from loader threads: invalidate is the one safe call.
            app.invalidate()

        self.live.changed = changed
        try:
            app.run()
        finally:
            self.live.changed = lambda: None
            self._app = None
        return result["item"]
