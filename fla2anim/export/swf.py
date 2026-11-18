"""SWF writer (default version 5 = Macromedia Flash 5 compatible).

Each unchanged run of a Flash layer becomes one DefineShape3 placed on a depth. Symbols are
flattened (the SWF contains plain shapes on a timeline). Bitmaps, text and masks are not exported.
Open the resulting .swf in Flash/Animate via File > Import to get an editable project.
"""
from __future__ import annotations

import struct
from pathlib import Path

from ..flatten import Evaluator, Fill, Img, Masked, Stroke, Text
from . import Options


class Bits:
    def __init__(self):
        self.buf = bytearray()
        self.cur = 0
        self.n = 0

    def ub(self, value, nbits):
        for i in range(nbits - 1, -1, -1):
            self.cur = (self.cur << 1) | ((value >> i) & 1)
            self.n += 1
            if self.n == 8:
                self.buf.append(self.cur)
                self.cur = self.n = 0

    def sb(self, value, nbits):
        self.ub(value & ((1 << nbits) - 1), nbits)

    def bytes(self):
        if self.n:
            self.buf.append(self.cur << (8 - self.n) & 0xFF)
            self.cur = self.n = 0
        return bytes(self.buf)


def sbits(*vals):
    n = 1
    for v in vals:
        n = max(n, (v if v >= 0 else -v - 1).bit_length() + 1)
    return n


def rect(xmin, xmax, ymin, ymax):
    nb = sbits(xmin, xmax, ymin, ymax)
    b = Bits()
    b.ub(nb, 5)
    for v in (xmin, xmax, ymin, ymax):
        b.sb(v, nb)
    return b.bytes()


def tag(code, body=b""):
    if len(body) < 63:
        return struct.pack("<H", (code << 6) | len(body)) + body
    return struct.pack("<HI", (code << 6) | 0x3F, len(body)) + body


def _tw(v):
    return int(round(v * 20))


def _rgba(c):
    return bytes(c)


def build_shape(ops):
    """DefineShape3 payload (without the shape id) for Fill/Stroke ops, plus its bounds."""
    fills = [op for op in ops if isinstance(op, Fill)]
    strokes = [op for op in ops if isinstance(op, Stroke)]
    xs, ys = [], []
    for op in fills:
        for loop in op.loops:
            xs += [_tw(p[0]) for p in loop]
            ys += [_tw(p[1]) for p in loop]
    for op in strokes:
        hw = int(max(op.width, 0.5) * 10) + 1
        xs += [_tw(p[0]) - hw for p in op.points] + [_tw(p[0]) + hw for p in op.points]
        ys += [_tw(p[1]) - hw for p in op.points] + [_tw(p[1]) + hw for p in op.points]
    bounds = (min(xs), max(xs), min(ys), max(ys))

    def style_array(items):
        n = len(items)
        return (bytes([n]) if n < 255 else b"\xff" + struct.pack("<H", n))

    body = bytearray(rect(*bounds))
    body += style_array(fills)
    for op in fills:
        body += b"\x00" + _rgba(op.rgba)
    body += style_array(strokes)
    for op in strokes:
        body += struct.pack("<H", max(1, min(65535, _tw(max(op.width, 0.5))))) + _rgba(op.rgba)
    nf, nl = len(fills).bit_length(), len(strokes).bit_length()
    b = Bits()
    b.ub(nf, 4)
    b.ub(nl, 4)

    def style_change(x, y, fill, line):
        b.ub(0, 1)       # non-edge record
        b.ub(0, 1)       # new styles
        b.ub(1, 1)       # line style
        b.ub(1, 1)       # fill style 1
        b.ub(0, 1)       # fill style 0
        b.ub(1, 1)       # move to
        nb = sbits(x, y)
        b.ub(nb, 5)
        b.sb(x, nb)
        b.sb(y, nb)
        b.ub(fill, nf)
        b.ub(line, nl)

    def line_to(dx, dy):
        k = max(abs(dx), abs(dy)) // 60000 + 1  # keep deltas inside 17 signed bits
        px = py = 0
        for i in range(1, k + 1):
            tx, ty = dx * i // k, dy * i // k
            sx, sy = tx - px, ty - py
            px, py = tx, ty
            if sx == 0 and sy == 0:
                continue
            nb = max(2, sbits(sx, sy))
            b.ub(1, 1)
            b.ub(1, 1)
            b.ub(nb - 2, 4)
            b.ub(1, 1)
            b.sb(sx, nb)
            b.sb(sy, nb)

    def path(points, close, fill, line):
        pts = [(_tw(x), _tw(y)) for x, y in points]
        if close and pts[0] != pts[-1]:
            pts.append(pts[0])
        style_change(pts[0][0], pts[0][1], fill, line)
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            line_to(x1 - x0, y1 - y0)

    for i, op in enumerate(fills, 1):
        for loop in op.loops:
            path(loop, True, i, 0)
    for j, op in enumerate(strokes, 1):
        if len(op.points) >= 2:
            path(op.points, False, 0, j)
    b.ub(0, 6)  # end of shape
    return bytes(body) + b.bytes(), bounds


def export_swf(doc, out, opts: Options, ev=None):
    if opts.swf_version < 3:
        raise ValueError("SWF version must be >= 3 (DefineShape3 with alpha is required)")
    ev = ev or Evaluator(doc)
    out = Path(out)
    if out.suffix.lower() != ".swf":
        out = out.with_suffix(".swf")
    out.parent.mkdir(parents=True, exist_ok=True)
    a, b = opts.frame_range(doc)
    n = b - a
    if n > 65535:
        raise ValueError("SWF supports at most 65535 frames")
    names = ev.slot_names()
    cur = [None] * len(names)  # active (ops, depth) per slot
    next_id = [1]
    tags = [tag(9, bytes(doc.bg[:3]))]
    skipped = set()

    def usable(ops):
        keep = []
        for op in ops:
            if isinstance(op, (Fill, Stroke)):
                keep.append(op)
            elif isinstance(op, Masked):
                skipped.add("masks (masked artwork exported unmasked)")
                keep.extend(usable(op.ops))
            elif isinstance(op, Img):
                skipped.add("bitmaps")
            elif isinstance(op, Text):
                skipped.add("text")
        return tuple(keep)

    for g in range(a, b):
        layers = ev.render_layers(g)
        for i in range(len(names)):
            ops = usable(layers[i][1]) if i < len(layers) else ()
            if cur[i] is not None and cur[i] == ops:
                continue
            if cur[i] is not None:
                tags.append(tag(28, struct.pack("<H", i + 1)))
                cur[i] = None
            if ops:
                body, bounds = build_shape(ops)
                cid = next_id[0]
                next_id[0] += 1
                tags.append(tag(32, struct.pack("<H", cid) + body))
                tags.append(tag(26, bytes([0x02]) + struct.pack("<HH", i + 1, cid)))
                cur[i] = ops
        tags.append(tag(1))
        opts.tick(g - a + 1, n)
    tags.append(tag(0))
    for s in sorted(skipped):
        doc.warn(f"SWF export does not include {s}")
    W, H = _tw(doc.width), _tw(doc.height)
    payload = rect(0, W, 0, H) + struct.pack("<HH", int(round(doc.fps * 256)), n) + b"".join(tags)
    data = b"FWS" + bytes([opts.swf_version]) + struct.pack("<I", 8 + len(payload)) + payload
    out.write_bytes(data)
    return [out]
