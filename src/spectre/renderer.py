"""Pure text-layout logic for the tactical HUD.

Kept separate from the hardware driver so every rendering decision — word
wrapping, message grouping, timestamp gutter, partial placement — can be unit
tested headlessly.

Data model
----------
The buffer holds a bounded list of finalised :class:`Message` objects (one per
radio transmission that the VAD closed) plus at most one live ``partial``
string being spoken right now.

The renderer lays these onto a grid of ``height_lines`` text rows of up to
``width_body_chars`` characters each, with the freshest content pinned to the
bottom of the screen and the oldest pushed off the top as more arrives.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    """One finalised radio transmission."""

    ts: float  # unix epoch seconds when the commit arrived
    text: str

    def hhmm(self) -> str:
        lt = time.localtime(self.ts)
        return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


@dataclass(frozen=True)
class RenderLine:
    """One physical row to draw on the display."""

    text: str
    kind: str  # "final" | "partial" | "separator"
    timestamp: str | None = None  # only non-empty on a message's first line


@dataclass(frozen=True)
class HudFrame:
    """Everything the display needs to paint one frame."""

    lines: list[RenderLine]
    rx_active: bool  # True while a partial is in flight
    last_final_ts: float | None  # for "last RX Ns ago" indicators


class TextBuffer:
    """Stateful buffer for the HUD.  Not thread safe; single-consumer."""

    def __init__(self, max_messages: int = 40) -> None:
        self._messages: deque[Message] = deque(maxlen=max_messages)
        self._partial: str = ""
        self._last_final_ts: float | None = None

    # ----- mutation -------------------------------------------------------

    def push_final(self, text: str, ts: float | None = None) -> None:
        text = text.strip()
        if not text:
            return
        when = ts if ts is not None else time.time()
        self._messages.append(Message(ts=when, text=text))
        self._last_final_ts = when
        self._partial = ""

    def push_partial(self, text: str) -> None:
        self._partial = text.strip()

    def clear(self) -> None:
        self._messages.clear()
        self._partial = ""
        self._last_final_ts = None

    # ----- rendering ------------------------------------------------------

    def render(self, width_body_chars: int, height_lines: int) -> HudFrame:
        """Produce a :class:`HudFrame` that fits ``height_lines`` rows.

        Layout (bottom-up):
            * If a partial is live, its text occupies the last row(s).
            * Above it, each message is rendered timestamp-gutter + wrapped
              body.  Message N's first line shows its ``HH:MM``; continuation
              lines leave the gutter blank.
            * A single blank "separator" row is inserted between messages so
              the operator can tell transmissions apart at a glance.
        """
        all_rows: list[RenderLine] = []

        # Render oldest → newest so we can trim from the top cleanly.
        for i, msg in enumerate(self._messages):
            if i > 0:
                all_rows.append(RenderLine(text="", kind="separator"))
            body_lines = _wrap(msg.text, width_body_chars)
            if not body_lines:
                continue
            all_rows.append(
                RenderLine(text=body_lines[0], kind="final", timestamp=msg.hhmm())
            )
            for line in body_lines[1:]:
                all_rows.append(RenderLine(text=line, kind="final"))

        # Partial sits below everything, no timestamp.  Give it a visible
        # marker "› " so it doesn't get confused with the last committed line.
        partial_rows: list[RenderLine] = []
        if self._partial:
            wrapped = _wrap("\u203a " + self._partial, width_body_chars)
            partial_rows = [
                RenderLine(text=t, kind="partial") for t in wrapped
            ]

        budget = max(0, height_lines - len(partial_rows))
        if len(all_rows) > budget:
            # Drop from the top; but don't leave a leading separator.
            all_rows = all_rows[-budget:]
            while all_rows and all_rows[0].kind == "separator":
                all_rows.pop(0)

        return HudFrame(
            lines=all_rows + partial_rows,
            rx_active=bool(self._partial),
            last_final_ts=self._last_final_ts,
        )


def _wrap(text: str, width_chars: int) -> list[str]:
    """Greedy word-wrap.  Words longer than the line are hard-broken."""
    if width_chars <= 0:
        return []
    lines: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > width_chars:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width_chars])
            word = word[width_chars:]
        if not current:
            current = word
            continue
        candidate = f"{current} {word}"
        if len(candidate) <= width_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
