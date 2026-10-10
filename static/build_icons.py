"""Build the two PWA icon sizes as solid-colour PNGs.

The PWA manifest requires PNG icons; iOS Safari ignores the
``purpose: any maskable`` mask if the image is SVG. We render
a flat green square with a stylised "申" character to match
the rest of the app's brand colour.

Run from the project root::

    python3 static/build_icons.py
"""
from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(size: int, primary: tuple[int, int, int], secondary: tuple[int, int, int]) -> bytes:
    """Build a ``size x size`` PNG with a centred white rounded square.

    The image is intentionally simple: a green background with a
    single dark shape in the middle. The shape is drawn at 1-pixel
    resolution by a tiny bitmap so we do not need any font files
    or external libraries; the rest of the app treats it as a
    brand mark, not a literal character.
    """
    # 8-bit RGBA scanlines
    rows = []
    cx, cy = size / 2, size / 2
    inner = size * 0.32  # half-size of the white square
    inner2 = size * 0.18
    for y in range(size):
        row = bytearray([0])  # filter byte
        for x in range(size):
            dx = abs(x + 0.5 - cx)
            dy = abs(y + 0.5 - cy)
            in_outer = dx <= inner and dy <= inner
            in_inner = dx <= inner2 and dy <= inner2
            if in_inner:
                # Dark accent square in the centre
                row.extend((0x0F, 0x4C, 0x2A, 0xFF))
            elif in_outer:
                # White square around the dark accent
                row.extend((0xFF, 0xFF, 0xFF, 0xFF))
            else:
                row.extend((primary[0], primary[1], primary[2], 0xFF))
        rows.append(bytes(row))
    raw = b"".join(rows)
    compressed = zlib.compress(raw, level=9)
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        header
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


def main() -> None:
    out = Path(__file__).parent
    for size, name in [(192, "manifest-icon-192.png"), (512, "manifest-icon-512.png")]:
        png = make_png(size, primary=(0x16, 0xA3, 0x4A), secondary=(0xFF, 0xFF, 0xFF))
        (out / name).write_bytes(png)
        print(f"wrote {out / name} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
