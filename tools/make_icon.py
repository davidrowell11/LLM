"""Generate Cortana's launcher icon.

Uses only the standard library (zlib + struct) so it runs before the
virtualenv exists and on a bare Crostini container -- the launcher installer
shouldn't need numpy or Pillow just to draw an icon.

Draws a 512x512 RGBA PNG: a squircle badge with a deep blue-violet gradient,
a "C" arc in a cyan-to-violet sweep with rounded caps, and a soft halo behind
it. Antialiasing is 3x3 supersampling plus smooth edge coverage, which the
ChromeOS launcher needs since it renders the icon fairly small.
"""

import math
import struct
import sys
import zlib

SIZE = 512
SAMPLES = 3

# Badge (superellipse: |x|^N + |y|^N <= 1 gives a rounded square)
BADGE_R = 236.0
BADGE_N = 4.2

# The C arc. Kept well inside the badge -- at launcher size an arc that
# reaches the edges reads as a cramped blob rather than a letter.
RING_MID = 134.0
RING_HALF = 27.0
GAP_DEG = 38.0  # half-width of the opening on the right

BG_TOP = (0x0D, 0x12, 0x30)
BG_BOTTOM = (0x24, 0x14, 0x52)
BG_ACCENT = (0x1B, 0x2C, 0x6B)

ARC_START = (0x2D, 0xE2, 0xE6)  # cyan
ARC_END = (0xA9, 0x8B, 0xFA)  # violet
HALO = (0x35, 0xC8, 0xF0)


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(lerp(a, b, t) for a, b in zip(c1, c2))


def smoothstep(edge0, edge1, x):
    if edge0 == edge1:
        return 0.0 if x < edge0 else 1.0
    t = min(max((x - edge0) / (edge1 - edge0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def arc_coverage(cx, cy):
    """Coverage of the C arc at a point, 0..1, with rounded end caps."""
    dist = math.hypot(cx, cy)
    angle = math.degrees(math.atan2(cy, cx))

    # Body of the arc, excluding the gap.
    if abs(angle) >= GAP_DEG:
        radial = 1.0 - smoothstep(RING_HALF - 2.0, RING_HALF + 1.0, abs(dist - RING_MID))
        # Fade the arc out as it approaches the gap, so the caps blend in.
        angular = smoothstep(GAP_DEG - 0.6, GAP_DEG + 0.6, abs(angle))
        body = radial * angular
    else:
        body = 0.0

    # Rounded caps at each end of the arc.
    cap = 0.0
    for sign in (1.0, -1.0):
        a = math.radians(GAP_DEG * sign)
        capx, capy = math.cos(a) * RING_MID, math.sin(a) * RING_MID
        d = math.hypot(cx - capx, cy - capy)
        cap = max(cap, 1.0 - smoothstep(RING_HALF - 2.0, RING_HALF + 1.0, d))

    return max(body, cap)


def sample(x, y):
    cx, cy = x - SIZE / 2, y - SIZE / 2

    # Squircle mask.
    norm = (abs(cx) / BADGE_R) ** BADGE_N + (abs(cy) / BADGE_R) ** BADGE_N
    badge = 1.0 - smoothstep(0.97, 1.03, norm)
    if badge <= 0.0:
        return (0.0, 0.0, 0.0, 0.0)

    # Background: vertical gradient, warmed slightly toward the upper left so
    # the badge reads as lit rather than flat.
    t = y / SIZE
    base = mix(BG_TOP, BG_BOTTOM, t)
    lightness = smoothstep(1.4, -0.5, (cx + cy) / SIZE)
    base = mix(base, BG_ACCENT, lightness * 0.45)

    # Soft highlight across the top edge, so the badge looks like a physical
    # surface catching light rather than a flat swatch.
    sheen = smoothstep(-BADGE_R * 0.95, -BADGE_R * 0.15, cy)
    base = mix(base, (0xFF, 0xFF, 0xFF), (1.0 - sheen) * 0.10)

    # Halo behind the arc.
    dist = math.hypot(cx, cy)
    glow = math.exp(-((dist - RING_MID) ** 2) / (2 * 92.0**2)) * 0.30
    base = mix(base, HALO, glow)

    # The arc itself, coloured by angle so it sweeps cyan -> violet.
    cov = arc_coverage(cx, cy)
    if cov > 0.0:
        angle = math.degrees(math.atan2(cy, cx))
        sweep = min(max((angle + 180.0) / 360.0, 0.0), 1.0)
        # Fold so both ends of the C are cyan and the far side is violet.
        sweep = 1.0 - abs(sweep - 0.5) * 2.0
        base = mix(base, mix(ARC_START, ARC_END, sweep), cov)

    return base + (255.0 * badge,)


def render():
    step = 1.0 / SAMPLES
    offset = step / 2
    total = SAMPLES * SAMPLES
    out = bytearray()

    for py in range(SIZE):
        for px in range(SIZE):
            r = g = b = a = 0.0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    s = sample(px + offset + sx * step, py + offset + sy * step)
                    # Premultiply so transparent edges don't bleed to black.
                    r += s[0] * s[3]
                    g += s[1] * s[3]
                    b += s[2] * s[3]
                    a += s[3]
            if a > 0:
                out += bytes(
                    (
                        min(255, round(r / a)),
                        min(255, round(g / a)),
                        min(255, round(b / a)),
                        min(255, round(a / total)),
                    )
                )
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def write_png(path, pixels):
    raw = b"".join(
        b"\x00" + pixels[y * SIZE * 4 : (y + 1) * SIZE * 4] for y in range(SIZE)
    )

    def chunk(kind, data):
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")

    with open(path, "wb") as handle:
        handle.write(png)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "cortana.png"
    write_png(out, render())
    print(f"wrote {out}")
