"""Generates the PNG app icons.

iOS ignores SVG for `apple-touch-icon`, and pulling in Pillow just for two
static images is not worth it, so the icon is rasterised here with nothing but
`zlib`. Rendered once during the image build and cached on disk afterwards.
"""

import struct
import zlib
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"
SIZES = (180, 512)
SUPERSAMPLE = 2

BG_TOP = (0x23, 0x2A, 0x36)
BG_BOTTOM = (0x0D, 0x0F, 0x14)
NOTE_LIGHT = (0xFF, 0x6B, 0x83)
NOTE_DARK = (0xFF, 0x2D, 0x55)
RING = (0xFF, 0x4D, 0x6D)

RING_RADIUS = 150.0
RING_WIDTH = 7.0
RING_ALPHA = 0.28

# Note geometry in a 512x512 design space.
HEADS = (((180.0, 342.0), 46.0, 38.0), ((296.0, 294.0), 46.0, 38.0))
STEMS = ((212.0, 232.0, 126.0, 342.0), (328.0, 348.0, 100.0, 294.0))
BEAM_X = (212.0, 348.0)
BEAM_TOP = 126.0
BEAM_HEIGHT = 54.0
BEAM_RISE = 26.0


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[float, float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _in_note(x: float, y: float) -> bool:
    for (cx, cy), rx, ry in HEADS:
        if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
            return True
    for x0, x1, y0, y1 in STEMS:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    if BEAM_X[0] <= x <= BEAM_X[1]:
        progress = (x - BEAM_X[0]) / (BEAM_X[1] - BEAM_X[0])
        top = BEAM_TOP - BEAM_RISE * progress
        if top <= y <= top + BEAM_HEIGHT:
            return True
    return False


def _sample(x: float, y: float) -> tuple[float, float, float]:
    """Colour at a point in the 512x512 design space."""
    diagonal = (x + y) / 1024.0
    colour = _lerp(BG_TOP, BG_BOTTOM, diagonal)

    distance = ((x - 256.0) ** 2 + (y - 256.0) ** 2) ** 0.5
    if abs(distance - RING_RADIUS) <= RING_WIDTH:
        colour = tuple(channel + (RING[i] - channel) * RING_ALPHA for i, channel in enumerate(colour))

    if _in_note(x, y):
        colour = _lerp(NOTE_LIGHT, NOTE_DARK, diagonal)

    return colour


def _render(size: int) -> bytes:
    """RGB pixel rows, supersampled for smooth edges."""
    scale = 512.0 / (size * SUPERSAMPLE)
    samples = SUPERSAMPLE * SUPERSAMPLE
    rows = bytearray()

    for py in range(size):
        rows.append(0)  # PNG filter type "none"
        row = bytearray()
        for px in range(size):
            red = green = blue = 0.0
            for sy in range(SUPERSAMPLE):
                y = ((py * SUPERSAMPLE) + sy + 0.5) * scale
                for sx in range(SUPERSAMPLE):
                    x = ((px * SUPERSAMPLE) + sx + 0.5) * scale
                    r, g, b = _sample(x, y)
                    red += r
                    green += g
                    blue += b
            row += bytes(
                (
                    min(255, max(0, round(red / samples))),
                    min(255, max(0, round(green / samples))),
                    min(255, max(0, round(blue / samples))),
                )
            )
        rows += row

    return bytes(rows)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _png(size: int, raw: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit truecolour RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def ensure_icons(force: bool = False) -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        target = ICON_DIR / f"icon-{size}.png"
        if target.exists() and not force:
            continue
        target.write_bytes(_png(size, _render(size)))


if __name__ == "__main__":
    ensure_icons(force=True)
    for icon_size in SIZES:
        print(f"geschrieben: {ICON_DIR / f'icon-{icon_size}.png'}")
