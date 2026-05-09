"""Display driver for the tactical HUD.

Three components share the same asyncio worker:

- :class:`ILI9341Display` — the real 320x240 SPI TFT via ``luma.lcd``.
- :class:`ConsoleDisplay`  — ANSI terminal output for laptop development.
- :func:`paint_frame`       — pure Pillow painter used by both the TFT
  backend and the offline mock-up generator in ``scripts/render_mocks.py``.

Each backend owns its own :class:`TextBuffer` so that wrap width, line height
and rendering rules can differ per device without tangling the data flow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from PIL import Image, ImageDraw, ImageFont

from .config import Config
from .renderer import HudFrame, TextBuffer
from .stt import Transcript

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tactical color palette                                                      #
# --------------------------------------------------------------------------- #
# Chosen for high contrast in daylight and minimum dark-adaptation damage at
# night.  Amber accents instead of blue to play well with NVG-friendly
# environments.

COLOR_BG = (0, 0, 0)
COLOR_BG_BAR = (18, 26, 18)  # very dark green-black
COLOR_RULE = (0, 120, 60)  # hairline under status bar
COLOR_STATUS_DIM = (120, 180, 120)  # muted phosphor green
COLOR_STATUS_BRIGHT = (90, 255, 120)  # active phosphor green
COLOR_RX_ON = (255, 64, 48)  # red TX/RX indicator
COLOR_RX_OFF = (80, 32, 32)  # RX pill when idle
COLOR_TS = (255, 176, 32)  # amber timestamps
COLOR_BODY = (240, 240, 240)  # near-white transcript body
COLOR_PARTIAL = (255, 192, 64)  # bright amber for live partials
COLOR_SEPARATOR = (30, 60, 30)  # thin line between messages


# --------------------------------------------------------------------------- #
# Painter                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class PaintSpec:
    """What :func:`paint_frame` needs besides the HudFrame itself."""

    width: int = 320
    height: int = 240
    body_font: ImageFont.FreeTypeFont = None  # type: ignore[assignment]
    bold_font: ImageFont.FreeTypeFont = None  # type: ignore[assignment]
    tiny_font: ImageFont.FreeTypeFont = None  # type: ignore[assignment]
    language: str = "UZ"


# Layout constants.  Measured against DejaVu Sans Mono Bold at 14pt on a
# 320x240 landscape panel.  If you change the fonts, re-measure.
STATUS_BAR_H = 36
MARGIN_X = 6
TS_GUTTER_W = 44  # leaves room for "HH:MM "
LINE_H = 18
SEPARATOR_H = 3


def paint_frame(frame: HudFrame, spec: PaintSpec) -> Image.Image:
    """Render ``frame`` into a new RGB Pillow image of the requested size."""
    img = Image.new("RGB", (spec.width, spec.height), color=COLOR_BG)
    draw = ImageDraw.Draw(img)

    _paint_status_bar(draw, spec, frame)
    _paint_body(draw, spec, frame)
    return img


def _paint_status_bar(
    draw: ImageDraw.ImageDraw, spec: PaintSpec, frame: HudFrame
) -> None:
    draw.rectangle((0, 0, spec.width, STATUS_BAR_H), fill=COLOR_BG_BAR)
    draw.line(
        (0, STATUS_BAR_H, spec.width, STATUS_BAR_H),
        fill=COLOR_RULE,
        width=1,
    )

    now = time.localtime()
    clock = f"{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}"

    # Row 1: [SPECTRE]     [RX pill]     [clock]
    draw.text(
        (MARGIN_X, 3),
        "SPECTRE",
        font=spec.bold_font,
        fill=COLOR_STATUS_BRIGHT,
    )

    rx_text = "● RX" if frame.rx_active else "○ RX"
    rx_fill = COLOR_RX_ON if frame.rx_active else COLOR_STATUS_DIM
    rx_w = _text_width(spec.bold_font, rx_text)
    rx_x = (spec.width - rx_w) // 2
    draw.text((rx_x, 3), rx_text, font=spec.bold_font, fill=rx_fill)

    clock_w = _text_width(spec.bold_font, clock)
    draw.text(
        (spec.width - clock_w - MARGIN_X, 3),
        clock,
        font=spec.bold_font,
        fill=COLOR_STATUS_BRIGHT,
    )

    # Row 2: [LANG]           [last RX age]
    lang = f"LANG {spec.language}"
    draw.text(
        (MARGIN_X, 19),
        lang,
        font=spec.tiny_font,
        fill=COLOR_STATUS_DIM,
    )

    age_text = _age_text(frame.last_final_ts)
    age_w = _text_width(spec.tiny_font, age_text)
    draw.text(
        (spec.width - age_w - MARGIN_X, 19),
        age_text,
        font=spec.tiny_font,
        fill=COLOR_STATUS_DIM,
    )


def _age_text(ts: float | None) -> str:
    if ts is None:
        return "IDLE"
    dt = max(0, int(time.time() - ts))
    if dt < 60:
        return f"LAST {dt}s"
    if dt < 3600:
        return f"LAST {dt // 60}m"
    return "LAST >1h"


def _paint_body(draw: ImageDraw.ImageDraw, spec: PaintSpec, frame: HudFrame) -> None:
    y = STATUS_BAR_H + 6
    bottom = spec.height - 2
    body_x = MARGIN_X + TS_GUTTER_W

    for line in frame.lines:
        if line.kind == "separator":
            mid_y = y + SEPARATOR_H // 2
            draw.line(
                (MARGIN_X + 2, mid_y, spec.width - MARGIN_X - 2, mid_y),
                fill=COLOR_SEPARATOR,
                width=1,
            )
            y += SEPARATOR_H
            continue

        if y + LINE_H > bottom:
            break

        if line.kind == "partial":
            draw.text(
                (MARGIN_X + 2, y),
                line.text,
                font=spec.body_font,
                fill=COLOR_PARTIAL,
            )
        else:  # final
            if line.timestamp:
                draw.text(
                    (MARGIN_X, y),
                    line.timestamp,
                    font=spec.tiny_font,
                    fill=COLOR_TS,
                )
            draw.text(
                (body_x, y),
                line.text,
                font=spec.body_font,
                fill=COLOR_BODY,
            )
        y += LINE_H


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


# --------------------------------------------------------------------------- #
# Font loading                                                                #
# --------------------------------------------------------------------------- #


_MONO_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]
_SANS_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _load_font(candidates: list[str], size: int):
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def default_spec(
    width: int = 320, height: int = 240, language: str = "UZ"
) -> PaintSpec:
    return PaintSpec(
        width=width,
        height=height,
        body_font=_load_font(_MONO_CANDIDATES, 14),
        bold_font=_load_font(_SANS_BOLD_CANDIDATES, 14),
        tiny_font=_load_font(_SANS_BOLD_CANDIDATES, 11),
        language=language.upper(),
    )


# --------------------------------------------------------------------------- #
# Layout constants exposed to the renderer                                    #
# --------------------------------------------------------------------------- #


# Monospace body at 14pt is ~8 px wide.  Landscape body area is roughly
# 320 - 6*2 - 44 = 264 px wide → ~32 cols.  Leave headroom for hinting drift.
BODY_WIDTH_CHARS = 32
# (height - status bar - top padding) / LINE_H.  Landscape: 240-36-8 = 196 → 10.
BODY_HEIGHT_LINES = (240 - STATUS_BAR_H - 8) // LINE_H  # = 10


# --------------------------------------------------------------------------- #
# Backend protocol                                                            #
# --------------------------------------------------------------------------- #


class Display(Protocol):
    """Sink for live transcripts.

    Owns whatever internal buffer/state it needs.  ``on_transcript`` is called
    for every partial + final event; ``tick`` is called roughly once a second
    so status-bar clocks and age counters stay alive even during silence.
    """

    def on_transcript(self, tr: Transcript) -> None: ...
    def tick(self) -> None: ...
    def close(self) -> None: ...


# --------------------------------------------------------------------------- #
# Console backend                                                             #
# --------------------------------------------------------------------------- #


class ConsoleDisplay:
    """Plain-text renderer for development on a laptop.

    Prints each committed transcript on its own line prefixed by its HH:MM,
    and overwrites a live partial in place via carriage return so the feed
    looks live.  Does not wrap, so long messages are readable.
    """

    _AMBER = "\033[38;5;214m"
    _RESET = "\033[0m"

    def __init__(self) -> None:
        self._use_color = sys.stdout.isatty() and os.getenv("NO_COLOR") is None
        self._partial_on_screen = False

    def on_transcript(self, tr: Transcript) -> None:
        if tr.final:
            self._clear_partial()
            hhmm = time.strftime("%H:%M", time.localtime())
            sys.stdout.write(f"[{hhmm}] {tr.text}\n")
            sys.stdout.flush()
        else:
            self._clear_partial()
            prefix = self._AMBER if self._use_color else ""
            suffix = self._RESET if self._use_color else ""
            sys.stdout.write(f"\r{prefix}\u203a {tr.text}{suffix}")
            sys.stdout.flush()
            self._partial_on_screen = True

    def tick(self) -> None:
        # Nothing to repaint in plain text mode — transcripts drive output.
        pass

    def close(self) -> None:
        self._clear_partial()
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _clear_partial(self) -> None:
        if self._partial_on_screen:
            sys.stdout.write("\r\033[K")
            self._partial_on_screen = False


# --------------------------------------------------------------------------- #
# ILI9341 backend                                                             #
# --------------------------------------------------------------------------- #


class ILI9341Display:
    """320x240 landscape TFT via luma.lcd.  Full framebuffer repaint.

    Full repaint is fine here — the ILI9341 over SPI @ 40 MHz can push a full
    RGB frame in ~40 ms, and we don't paint faster than transcripts arrive
    (at most every few hundred ms).
    """

    def __init__(self, cfg: Config) -> None:
        from luma.core.interface.serial import spi  # type: ignore
        from luma.lcd.device import ili9341  # type: ignore

        serial = spi(
            port=cfg.spi_port,
            device=cfg.spi_device,
            gpio_DC=cfg.gpio_dc,
            gpio_RST=cfg.gpio_rst,
        )
        self._device = ili9341(serial, rotate=cfg.display_rotate)
        self._spec = default_spec(
            width=self._device.width,
            height=self._device.height,
            language=cfg.language,
        )
        self._buffer = TextBuffer(max_messages=max(8, cfg.buffer_words // 10))
        self._dirty = True
        log.info(
            "display: ILI9341 %dx%d rotate=%d",
            self._device.width,
            self._device.height,
            cfg.display_rotate,
        )

    def on_transcript(self, tr: Transcript) -> None:
        if tr.final:
            self._buffer.push_final(tr.text)
        else:
            self._buffer.push_partial(tr.text)
        self._dirty = True
        self._paint()

    def tick(self) -> None:
        # Repaint even without new events so the clock and LAST-age counter
        # stay live.
        self._paint()

    def _paint(self) -> None:
        frame = self._buffer.render(BODY_WIDTH_CHARS, BODY_HEIGHT_LINES)
        img = paint_frame(frame, self._spec)
        self._device.display(img)

    def close(self) -> None:
        try:
            self._device.cleanup()
        except Exception as exc:  # noqa: BLE001
            log.debug("display: cleanup raised: %s", exc)


# --------------------------------------------------------------------------- #
# Linux framebuffer backend (/dev/fb1 from fbtft)                             #
# --------------------------------------------------------------------------- #


class FramebufferDisplay:
    """Paint into a Linux framebuffer device — typically ``/dev/fb1``.

    On Pi OS Trixie the recommended way to drive a small SPI TFT is via the
    ``fbtft`` dtoverlay, which exposes the panel as ``/dev/fb1``.  The kernel
    handles the SPI traffic, panel init, reset pulse and backlight pin for us;
    we just hand it a fully-rendered frame.

    Pixel format is little-endian RGB565 — that's what fbtft expects from
    user-space and what every Linux framebuffer ioctl reports for these
    panels.  We auto-detect the panel size from
    ``/sys/class/graphics/<fbN>/virtual_size`` so the same code works whether
    the operator has the overlay set to ``rotate=90`` (landscape) or
    ``rotate=0`` (portrait).
    """

    def __init__(self, cfg: Config) -> None:
        import numpy as np  # local import: keeps display importable on bare CI

        self._np = np
        fb_path = cfg.fb_path
        sysfs = "/sys/class/graphics/" + os.path.basename(fb_path)

        try:
            with open(f"{sysfs}/virtual_size") as f:
                w_str, h_str = f.read().strip().split(",", 1)
                width, height = int(w_str), int(h_str)
        except OSError as exc:
            raise RuntimeError(
                f"cannot read {sysfs}/virtual_size: {exc}.  Is the fbtft "
                f"dtoverlay enabled in /boot/firmware/config.txt and the Pi "
                f"rebooted?"
            ) from exc

        try:
            with open(f"{sysfs}/bits_per_pixel") as f:
                bpp = int(f.read().strip())
        except OSError:
            bpp = 16
        if bpp != 16:
            raise RuntimeError(
                f"{fb_path}: only 16bpp RGB565 supported, got {bpp}bpp"
            )

        try:
            self._fb = open(fb_path, "wb", buffering=0)  # noqa: SIM115
        except OSError as exc:
            raise RuntimeError(
                f"cannot open {fb_path}: {exc}.  The user running spectre "
                f"must be in the 'video' group."
            ) from exc

        self._width = width
        self._height = height
        self._spec = default_spec(width=width, height=height, language=cfg.language)
        self._buffer = TextBuffer(max_messages=max(8, cfg.buffer_words // 10))
        log.info(
            "display: %s %dx%d 16bpp (kernel framebuffer)",
            fb_path,
            width,
            height,
        )

    def on_transcript(self, tr: Transcript) -> None:
        if tr.final:
            self._buffer.push_final(tr.text)
        else:
            self._buffer.push_partial(tr.text)
        self._paint()

    def tick(self) -> None:
        self._paint()

    def _paint(self) -> None:
        # Body wrap is computed against the actual panel width so a
        # portrait-rotated overlay still wraps reasonably.
        body_chars = max(8, (self._width - 12 - TS_GUTTER_W) // 8)
        body_lines = max(1, (self._height - STATUS_BAR_H - 8) // LINE_H)
        frame = self._buffer.render(body_chars, body_lines)
        img = paint_frame(frame, self._spec)
        raw = self._rgb888_to_rgb565(img)
        try:
            self._fb.seek(0)
            self._fb.write(raw)
        except OSError as exc:
            log.warning("display: fb write failed: %s", exc)

    def _rgb888_to_rgb565(self, img: Image.Image) -> bytes:
        np = self._np
        arr = np.asarray(img.convert("RGB"), dtype=np.uint32)
        r = (arr[..., 0] & 0xF8) << 8
        g = (arr[..., 1] & 0xFC) << 3
        b = (arr[..., 2] & 0xF8) >> 3
        # Force little-endian uint16 regardless of host endianness — fbtft
        # always reads RGB565 lo-byte-first from user-space.
        return (r | g | b).astype("<u2").tobytes()

    def close(self) -> None:
        try:
            self._fb.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("display: fb close raised: %s", exc)


# --------------------------------------------------------------------------- #
# Factory + worker                                                            #
# --------------------------------------------------------------------------- #


def make_display(cfg: Config) -> Display:
    backend = cfg.display.lower()
    if backend == "fb1":
        return FramebufferDisplay(cfg)
    if backend == "ili9341":
        return ILI9341Display(cfg)
    if backend == "console":
        return ConsoleDisplay()
    raise ValueError(f"unknown SPECTRE_DISPLAY={cfg.display!r}")


async def run_display(
    cfg: Config,
    text_in: asyncio.Queue[Transcript],
    stop: asyncio.Event,
) -> None:
    """Consume transcripts and drive the chosen backend.

    A 1-second timeout on ``text_in.get()`` also doubles as the tick cadence
    so clocks and age counters in the status bar stay live during silence.
    """
    display = make_display(cfg)
    try:
        while not stop.is_set():
            try:
                tr = await asyncio.wait_for(text_in.get(), timeout=1.0)
                log.debug("display: rx final=%s text=%r", tr.final, tr.text)
                display.on_transcript(tr)
            except asyncio.TimeoutError:
                display.tick()
    finally:
        display.close()


async def run_display_status(
    cfg: Config, message: str, stop: asyncio.Event
) -> None:
    """Paint a single static status message to the HUD until ``stop`` is set.

    Used when Spectre can't start normally (e.g. missing API key) so the
    operator sees a readable error on the TFT instead of a dead black screen
    or an endless systemd restart loop.
    """
    display = make_display(cfg)
    try:
        display.on_transcript(Transcript(text=message, final=True))
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                display.tick()
    finally:
        display.close()


# --------------------------------------------------------------------------- #
# Helper for mock-ups / tests                                                 #
# --------------------------------------------------------------------------- #

_MessageT = Optional[Transcript]  # kept to avoid breaking re-exports
