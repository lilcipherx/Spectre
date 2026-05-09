"""Render tactical HUD mockups using the new renderer + painter (landscape)."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "/home/ubuntu/Spectre/src")

from PIL import Image, ImageDraw, ImageFont

from spectre.display import BODY_HEIGHT_LINES, BODY_WIDTH_CHARS, default_spec, paint_frame
from spectre.renderer import TextBuffer

OUT = os.environ.get("SPECTRE_MOCKS_OUT", "/tmp/mocks2")
os.makedirs(OUT, exist_ok=True)

T0 = time.mktime((2025, 4, 23, 14, 27, 30, 0, 0, 0))


def render_state(name: str, build_buf) -> None:
    buf: TextBuffer = build_buf()
    frame = buf.render(BODY_WIDTH_CHARS, BODY_HEIGHT_LINES)
    spec = default_spec(language="UZ")  # 320x240 landscape default
    img = paint_frame(frame, spec)
    img.save(f"{OUT}/{name}.png")
    print(f"saved {name}.png {img.size}")


def scene_midstream() -> TextBuffer:
    b = TextBuffer()
    b.push_final("Assalomu alaykum.", ts=T0 + 0)
    b.push_final("Bugun havo juda yaxshi, yomg'ir yo'q.", ts=T0 + 25)
    b.push_final("Birinchi guruh shimolga yurgin.", ts=T0 + 58)
    b.push_final("Ikkinchi guruh janubga.", ts=T0 + 92)
    b.push_partial("hozir pozitsiyamizni aniqlayapmiz")
    return b


def scene_idle() -> TextBuffer:
    return TextBuffer()


def scene_full() -> TextBuffer:
    b = TextBuffer()
    b.push_final(
        "Pozitsiyamiz xavfsiz va himoya ostida. Barcha guruh a'zolari "
        "o'z joylarida. Hech qanday g'alati harakat yo'q. Keyingi "
        "buyruqni kutamiz.",
        ts=T0 + 120,
    )
    return b


def scene_offline() -> TextBuffer:
    b = TextBuffer()
    b.push_final("Assalomu alaykum.", ts=T0)
    b.push_final("Bugun havo juda yaxshi.", ts=T0 + 30)
    b.push_partial("[connection lost]")
    return b


def scene_short() -> TextBuffer:
    b = TextBuffer()
    b.push_final("Tayyormiz. Tugadi.", ts=T0 + 150)
    return b


render_state("1_midstream", scene_midstream)
render_state("2_idle", scene_idle)
render_state("3_full", scene_full)
render_state("4_offline", scene_offline)
render_state("5_short", scene_short)

# Upscale 3x, add bezel
titles = {
    "1_midstream": "Mid-transmission (live partial in amber)",
    "2_idle": "Idle — no traffic yet",
    "3_full": "Long message fills the screen",
    "4_offline": "Network drop (auto-reconnect)",
    "5_short": "Short confirmation message",
}

BEZEL = 20
cells: dict[str, Image.Image] = {}
for name in titles:
    src = Image.open(f"{OUT}/{name}.png")
    big = src.resize((src.width * 3, src.height * 3), Image.NEAREST)
    canvas = Image.new(
        "RGB", (big.width + BEZEL * 2, big.height + BEZEL * 2), color=(22, 22, 22)
    )
    canvas.paste(big, (BEZEL, BEZEL))
    d = ImageDraw.Draw(canvas)
    d.rectangle(
        (0, 0, canvas.width - 1, canvas.height - 1), outline=(70, 70, 70), width=2
    )
    canvas.save(f"{OUT}/{name}_3x.png")
    cells[name] = canvas

# 2x2 grid of the four most interesting scenes.
grid_order = ["1_midstream", "3_full", "4_offline", "5_short"]
cell_w = cells[grid_order[0]].width
cell_h = cells[grid_order[0]].height
TITLE_H = 34
GAP = 24
grid_w = cell_w * 2 + GAP * 3
grid_h = (cell_h + TITLE_H) * 2 + GAP * 3
grid = Image.new("RGB", (grid_w, grid_h), color=(12, 12, 12))
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
d = ImageDraw.Draw(grid)
for i, name in enumerate(grid_order):
    col = i % 2
    row = i // 2
    x = GAP + col * (cell_w + GAP)
    y = GAP + row * (cell_h + TITLE_H + GAP)
    d.text((x + 8, y + 2), titles[name], font=font, fill="white")
    grid.paste(cells[name], (x, y + TITLE_H))
grid.save(f"{OUT}/grid.png")
print(f"grid: {grid.size}")
