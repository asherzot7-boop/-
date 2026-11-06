"""Draw the Nam Tour icon (an 8-point star on ink) and save it as a multi-size .ico. Pure Python, no Pillow.

    python make_icon.py namtour.ico
"""
from __future__ import annotations

import math
import struct
import sys
import zlib

INK, TURQ, SAFFRON = (11, 20, 38), (69, 201, 195), (232, 176, 75)


def _star(cx, cy, r_out, r_in, rot):
    return [(cx + math.cos(rot + i * math.pi / 8) * (r_out if i % 2 == 0 else r_in),
             cy + math.sin(rot + i * math.pi / 8) * (r_out if i % 2 == 0 else r_in)) for i in range(16)]


def _inside(x, y, poly):
    hit = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def _pixel(x, y, s):
    """RGBA of one sample at (x, y) on an s×s canvas."""
    r = s * 0.22                                              # rounded square corner radius
    dx, dy = max(r - x, 0, x - (s - r)), max(r - y, 0, y - (s - r))
    if dx * dx + dy * dy > r * r:
        return (0, 0, 0, 0)
    c = s / 2
    d = math.hypot(x - c, y - c)
    if _inside(x, y, _star(c, c, s * 0.40, s * 0.25, -math.pi / 2)):
        return (*SAFFRON, 255) if d < s * 0.085 else (*TURQ, 255)
    if abs(d - s * 0.43) < s * 0.018:
        return (*SAFFRON, 255)
    return (*INK, 255)


def render(s: int, ss: int = 4) -> bytes:
    rows = []
    for y in range(s):
        row = bytearray([0])                                  # PNG filter type 0
        for x in range(s):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    px = _pixel(x + (sx + 0.5) / ss, y + (sy + 0.5) / ss, s)
                    for k in range(3):
                        acc[k] += px[k] * px[3]
                    acc[3] += px[3]
            a = acc[3] // (ss * ss)
            row += bytes([acc[0] // max(acc[3], 1), acc[1] // max(acc[3], 1), acc[2] // max(acc[3], 1), a])
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 32, 48, 256)) -> bytes:
    images = [(s, render(s, 4 if s <= 48 else 2)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, png in images:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    return head + entries + blobs


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "namtour.ico"
    with open(out, "wb") as f:
        f.write(ico())
    print(out)
