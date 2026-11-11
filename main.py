# Cutest Cat — a program that draws one cute kitten, front-facing, and writes it
# to /output/entry.png (or $MAKEFUN_OUTPUT_DIR/entry.png).
# Python 3.13, standard library only. Fully deterministic: no randomness, no clock,
# no network, no input files. The picture is drawn from vector shapes with soft
# (feathered) edges, then encoded as a PNG with zlib.
import math
import os
import struct
import zlib

W = 1024
H = 1024


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class Canvas:
    """An RGB canvas backed by a bytearray, with feathered vector shapes."""

    def __init__(self, w, h, bg):
        self.w = w
        self.h = h
        self.px = bytearray(w * h * 3)
        r, g, b = bg
        for i in range(0, len(self.px), 3):
            self.px[i] = r
            self.px[i + 1] = g
            self.px[i + 2] = b

    def blend(self, x, y, color, alpha):
        """Blend one pixel toward color by alpha in [0, 1]."""
        if alpha <= 0.0 or x < 0 or y < 0 or x >= self.w or y >= self.h:
            return
        i = (y * self.w + x) * 3
        a = alpha
        self.px[i] = int(self.px[i] * (1 - a) + color[0] * a)
        self.px[i + 1] = int(self.px[i + 1] * (1 - a) + color[1] * a)
        self.px[i + 2] = int(self.px[i + 2] * (1 - a) + color[2] * a)

    def vgrad(self, top, bottom):
        """Vertical gradient, blended over the whole canvas."""
        for y in range(self.h):
            t = y / max(1, self.h - 1)
            c = tuple(int(top[k] * (1 - t) + bottom[k] * t) for k in range(3))
            base = y * self.w * 3
            for x in range(self.w):
                i = base + x * 3
                self.px[i], self.px[i + 1], self.px[i + 2] = c

    def ellipse(self, cx, cy, rx, ry, color, alpha=1.0, feather=1.6):
        """Filled ellipse with a soft edge. feather is in pixels."""
        x0 = max(0, int(cx - rx - feather - 1))
        x1 = min(self.w - 1, int(cx + rx + feather + 1))
        y0 = max(0, int(cy - ry - feather - 1))
        y1 = min(self.h - 1, int(cy + ry + feather + 1))
        fx = feather / rx
        fy = feather / ry
        for y in range(y0, y1 + 1):
            dy = (y - cy) / ry
            dy2 = dy * dy
            row = y * self.w * 3
            for x in range(x0, x1 + 1):
                dx = (x - cx) / rx
                d = dx * dx + dy2
                if d >= 1.0 + max(fx, fy):
                    continue
                cov = clamp((1.0 - d) / (2.0 * max(fx, fy) + 1e-9), 0.0, 1.0)
                a = alpha * cov
                if a <= 0.0:
                    continue
                i = row + x * 3
                self.px[i] = int(self.px[i] * (1 - a) + color[0] * a)
                self.px[i + 1] = int(self.px[i + 1] * (1 - a) + color[1] * a)
                self.px[i + 2] = int(self.px[i + 2] * (1 - a) + color[2] * a)

    def polygon(self, pts, color, alpha=1.0, feather=1.6):
        """Filled polygon with a soft edge (even-odd not needed: simple convex-ish shapes)."""
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0 = max(0, int(min(xs) - feather - 1))
        x1 = min(self.w - 1, int(max(xs) + feather + 1))
        y0 = max(0, int(min(ys) - feather - 1))
        y1 = min(self.h - 1, int(max(ys) + feather + 1))
        n = len(pts)
        for y in range(y0, y1 + 1):
            row = y * self.w * 3
            for x in range(x0, x1 + 1):
                inside = False
                j = n - 1
                min_dist = 1e9
                for k in range(n):
                    x1p, y1p = pts[j]
                    x2p, y2p = pts[k]
                    if (y1p > y) != (y2p > y):
                        xin = x1p + (y - y1p) * (x2p - x1p) / (y2p - y1p)
                        if x < xin:
                            inside = not inside
                    # distance point-to-segment
                    vx, vy = x2p - x1p, y2p - y1p
                    wx, wy = x - x1p, y - y1p
                    L2 = vx * vx + vy * vy
                    t = 0.0 if L2 == 0 else clamp((wx * vx + wy * vy) / L2, 0.0, 1.0)
                    px_, py_ = x1p + t * vx, y1p + t * vy
                    dd = math.hypot(x - px_, y - py_)
                    if dd < min_dist:
                        min_dist = dd
                    j = k
                if not inside and min_dist > feather:
                    continue
                # Inside: fade in over `feather` px from the edge; outside: fade out.
                cov = clamp(min_dist / feather, 0.0, 1.0) if inside else clamp(1.0 - min_dist / feather, 0.0, 1.0)
                a = alpha * cov
                if a <= 0.0:
                    continue
                i = row + x * 3
                self.px[i] = int(self.px[i] * (1 - a) + color[0] * a)
                self.px[i + 1] = int(self.px[i + 1] * (1 - a) + color[1] * a)
                self.px[i + 2] = int(self.px[i + 2] * (1 - a) + color[2] * a)

    def thick_line(self, x1, y1, x2, y2, width, color, alpha=1.0):
        """A line with round caps, soft edge."""
        r = width / 2.0
        x0 = max(0, int(min(x1, x2) - r - 2))
        x1b = min(self.w - 1, int(max(x1, x2) + r + 2))
        y0 = max(0, int(min(y1, y2) - r - 2))
        y1b = min(self.h - 1, int(max(y1, y2) + r + 2))
        vx, vy = x2 - x1, y2 - y1
        L2 = vx * vx + vy * vy
        for y in range(y0, y1b + 1):
            row = y * self.w * 3
            for x in range(x0, x1b + 1):
                t = 0.0 if L2 == 0 else clamp(((x - x1) * vx + (y - y1) * vy) / L2, 0.0, 1.0)
                px_, py_ = x1 + t * vx, y1 + t * vy
                d = math.hypot(x - px_, y - py_)
                if d > r + 1.2:
                    continue
                cov = clamp((r + 1.2 - d) / 2.4, 0.0, 1.0)
                a = alpha * cov
                if a <= 0.0:
                    continue
                i = row + x * 3
                self.px[i] = int(self.px[i] * (1 - a) + color[0] * a)
                self.px[i + 1] = int(self.px[i + 1] * (1 - a) + color[1] * a)
                self.px[i + 2] = int(self.px[i + 2] * (1 - a) + color[2] * a)

    def polyline(self, pts, width, color, alpha=1.0):
        for a, b in zip(pts, pts[1:]):
            self.thick_line(a[0], a[1], b[0], b[1], width, color, alpha)


def write_png(path, canvas):
    """Encode the canvas as a 24-bit PNG (filter 0 per row), stdlib only."""
    w, h = canvas.w, canvas.h
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += canvas.px[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


# ---- palette ----
PEACH_TOP = (253, 236, 214)
PEACH_BOT = (250, 222, 190)
HALO = (255, 248, 236)
FUR = (242, 168, 92)        # ginger
FUR_LIGHT = (251, 227, 194)  # cream muzzle / chest
FUR_DARK = (224, 142, 62)    # stripes
EAR_PINK = (244, 154, 193)
NOSE = (232, 93, 117)
EYE = (59, 42, 32)
BLUSH = (244, 154, 193)
WHISKER = (120, 84, 60)
BOW = (232, 93, 117)


def rot(px, py, cx, cy, ang):
    """Rotate point (px, py) around (cx, cy) by ang radians."""
    c, s = math.cos(ang), math.sin(ang)
    dx, dy = px - cx, py - cy
    return (cx + dx * c - dy * s, cy + dx * s + dy * c)


def draw_cat(cv):
    # Background: warm peach gradient + a soft halo behind the head.
    cv.vgrad(PEACH_TOP, PEACH_BOT)
    cv.ellipse(512, 540, 430, 400, HALO, alpha=0.55)

    # The whole face is tilted ~7 degrees for a head-tilt charm.
    TILT = math.radians(-7)
    CX, CY = 512, 560

    def R(x, y):
        return rot(x, y, CX, CY, TILT)

    # Ears: rounded triangles, tilted outward; pink inner ears.
    for sgn in (-1, 1):
        # ear corners in unrotated space, then rotate each point
        ex = CX + sgn * 195
        ear = [R(ex - sgn * 55, 380),
               R(ex + sgn * 115, 250),
               R(ex + sgn * 165, 400)]
        cv.polygon(ear, FUR)
        inner = [R(ex - sgn * 28, 372),
                 R(ex + sgn * 88, 282),
                 R(ex + sgn * 128, 384)]
        cv.polygon(inner, EAR_PINK)

    # Head: big round ginger oval.
    cv.ellipse(CX, CY, 330, 298, FUR)

    # Forehead stripes: three short vertical tabby bars.
    for dx, yy in ((-78, 322), (0, 310), (78, 322)):
        x, y = R(CX + dx, yy)
        cv.ellipse(x, y, 14, 32, FUR_DARK, alpha=0.5)

    # Eyes: big dark ovals with a large white highlight and a tiny dot.
    for sgn in (-1, 1):
        ex, ey = R(CX + sgn * 128, 545)
        cv.ellipse(ex, ey, 64, 80, (255, 255, 255))
        cv.ellipse(ex, ey + 6, 56, 70, EYE)
        cv.ellipse(ex - 18, ey - 26, 20, 26, (255, 255, 255))
        cv.ellipse(ex + 16, ey + 30, 8, 10, (255, 255, 255), alpha=0.9)

    # Muzzle: one wide cream patch under the nose; pink nose; little mouth.
    mx, my = R(CX, 700)
    cv.ellipse(mx, my, 168, 74, FUR_LIGHT)
    nx, ny = R(CX, 648)
    cv.polygon([(nx - 30, ny - 12), (nx + 30, ny - 12), (nx, ny + 26)], NOSE)
    # mouth: two small curves under the nose
    cv.polyline([R(CX - 2, 676), R(CX - 26, 700),
                 R(CX - 52, 690)], 7, WHISKER)
    cv.polyline([R(CX + 2, 676), R(CX + 26, 700),
                 R(CX + 52, 690)], 7, WHISKER)

    # Blush cheeks.
    for sgn in (-1, 1):
        bx, by = R(CX + sgn * 218, 668)
        cv.ellipse(bx, by, 42, 26, BLUSH, alpha=0.55)

    # Whiskers: three per side, gently curved, starting at the muzzle edge.
    for sgn in (-1, 1):
        base_x, base_y = R(CX + sgn * 148, 682)
        for dy, spread in ((-24, 128), (0, 142), (24, 128)):
            ex, ey = R(CX + sgn * (148 + spread), 682 + dy)
            mx2, my2 = R(CX + sgn * (148 + spread * 0.55), 682 + dy * 0.55)
            cv.polyline([(base_x, base_y), (mx2, my2), (ex, ey)], 4, WHISKER, alpha=0.75)

    # Bow on the right ear: two triangles + knot (charm touch).
    bx, by = R(CX + 245, 330)
    cv.polygon([(bx - 52, by - 26), (bx - 6, by), (bx - 52, by + 26)], BOW)
    cv.polygon([(bx + 52, by - 26), (bx + 6, by), (bx + 52, by + 26)], BOW)
    cv.ellipse(bx, by, 16, 14, (206, 62, 96))


def main():
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    cv = Canvas(W, H, PEACH_TOP)
    draw_cat(cv)
    path = os.path.join(out_dir, "entry.png")
    n = write_png(path, cv)
    print(f"wrote {path} ({n} bytes)")


if __name__ == "__main__":
    main()
