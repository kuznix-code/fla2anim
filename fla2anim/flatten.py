"""Evaluate a Flash timeline at a given frame into a flat list of draw operations.

All coordinates in ops are final document pixels (origin top-left, y down).
Ops are frozen dataclasses so equal frames compare/hash equal (used to find keyframes).
"""
from __future__ import annotations

import dataclasses
import io
import math
from dataclasses import dataclass, field

from .model import (
    IDENT, ColorFx, Group, Instance, Prim, Shape, TextBox, mat_apply, mat_mul, mat_scale,
)

MAX_DEPTH = 24


@dataclass(frozen=True)
class Fill:
    loops: tuple  # tuple of loops, each a tuple of (x, y)
    rgba: tuple


@dataclass(frozen=True)
class Stroke:
    points: tuple
    width: float
    rgba: tuple


@dataclass(frozen=True)
class Img:
    key: str
    matrix: tuple
    alpha: float
    image: object = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class Text:
    text: str
    x: float
    y: float
    size: float
    rgba: tuple


@dataclass(frozen=True)
class Masked:
    mask: tuple
    ops: tuple


def _r(v):
    return round(v, 3)


def apply_fx(rgba, fxs):
    r, g, b, a = (float(v) for v in rgba)
    for fx in fxs:
        r = r * fx.r_mul + fx.r_off
        g = g * fx.g_mul + fx.g_off
        b = b * fx.b_mul + fx.b_off
        a = a * fx.alpha_mul + fx.alpha_off
        if fx.tint is not None and fx.tint_mul:
            t = fx.tint_mul
            r, g, b = (r * (1 - t) + fx.tint[0] * t, g * (1 - t) + fx.tint[1] * t,
                       b * (1 - t) + fx.tint[2] * t)
        if fx.brightness > 0:
            k = fx.brightness
            r, g, b = r + (255 - r) * k, g + (255 - g) * k, b + (255 - b) * k
        elif fx.brightness < 0:
            k = 1 + fx.brightness
            r, g, b = r * k, g * k, b * k
    return tuple(int(max(0, min(255, round(v)))) for v in (r, g, b, a))


def _decompose(m):
    a, b, c, d, tx, ty = m
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    if (a * d - b * c) < 0:
        sy = -sy
    return math.atan2(b, a), sx, sy, tx, ty


def _compose(rot, sx, sy, tx, ty):
    return (math.cos(rot) * sx, math.sin(rot) * sx, -math.sin(rot) * sy, math.cos(rot) * sy, tx, ty)


def _ease(p, e):
    if e > 0:
        return 1 - (1 - p) ** (1 + e / 50.0)
    if e < 0:
        return p ** (1 + (-e) / 50.0)
    return p


def interp_matrix(m0, m1, p, rotate="none", times=0):
    r0, sx0, sy0, tx0, ty0 = _decompose(m0)
    r1, sx1, sy1, tx1, ty1 = _decompose(m1)
    dr = (r1 - r0 + math.pi) % (2 * math.pi) - math.pi
    if rotate == "clockwise":
        dr = (r1 - r0) % (2 * math.pi) + 2 * math.pi * times
    elif rotate == "counter-clockwise":
        dr = -((r0 - r1) % (2 * math.pi)) - 2 * math.pi * times
    lerp = lambda u, v: u + (v - u) * p
    return _compose(r0 + dr * p, lerp(sx0, sx1), lerp(sy0, sy1), lerp(tx0, tx1), lerp(ty0, ty1))


def _quad_points(p0, c, p1, n):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0],
                    u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]))
    return out


def _seg_points(seg, m):
    """Transformed points of a segment, excluding its start point."""
    p = seg.pts
    if seg.kind == "L":
        return [mat_apply(m, p[2], p[3])]
    a = mat_apply(m, p[0], p[1])
    c = mat_apply(m, p[2], p[3])
    b = mat_apply(m, p[4], p[5])
    chord = math.hypot(b[0] - a[0], b[1] - a[1]) + math.hypot(c[0] - a[0], c[1] - a[1])
    return _quad_points(a, c, b, max(3, min(32, int(chord / 3) + 2)))


def _rev(seg):
    p = seg.pts
    if seg.kind == "L":
        return type(seg)("L", (p[2], p[3], p[0], p[1]))
    return type(seg)("Q", (p[4], p[5], p[2], p[3], p[0], p[1]))


def _key(x, y):
    return (round(x, 2), round(y, 2))


def chain_loops(segs):
    """Join directed segments into closed loops (lists of segs)."""
    starts = {}
    for i, s in enumerate(segs):
        starts.setdefault(_key(s.pts[0], s.pts[1]), []).append(i)
    used = [False] * len(segs)
    loops = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        loop = [segs[i]]
        start = _key(*segs[i].pts[:2])
        cur = _key(*segs[i].pts[-2:])
        while cur != start:
            cand = starts.get(cur)
            nxt = None
            while cand:
                j = cand.pop()
                if not used[j]:
                    nxt = j
                    break
            if nxt is None:
                break
            used[nxt] = True
            loop.append(segs[nxt])
            cur = _key(*segs[nxt].pts[-2:])
        loops.append(loop)
    return loops


def _loop_points(loop, m):
    pts = [mat_apply(m, loop[0].pts[0], loop[0].pts[1])]
    for s in loop:
        pts.extend(_seg_points(s, m))
    return tuple((_r(x), _r(y)) for x, y in pts)


def _ellipse_like(prim):
    """Closed polygon (local coords) for a rect/oval primitive."""
    x, y, w, h = prim.x, prim.y, prim.w, prim.h
    if prim.kind == "oval":
        n = 48
        return [(x + w / 2 + w / 2 * math.cos(2 * math.pi * i / n),
                 y + h / 2 + h / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    tl, tr, br, bl = (max(0.0, min(r, w / 2, h / 2)) for r in prim.radii)
    pts = []

    def arc(cx, cy, r, a0):
        if r <= 0:
            pts.append((cx, cy))
            return
        for i in range(9):
            a = math.radians(a0 + 90 * i / 8)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    arc(x + tl, y + tl, tl, 180)
    arc(x + w - tr, y + tr, tr, 270)
    arc(x + w - br, y + h - br, br, 0)
    arc(x + bl, y + h - bl, bl, 90)
    return pts


class Evaluator:
    def __init__(self, doc):
        self.doc = doc
        self._bitmaps = {}

    # ----- timeline bookkeeping -------------------------------------------------
    def locate(self, g):
        """Global frame -> (timeline, local frame)."""
        for tl in self.doc.timelines:
            if g < tl.length:
                return tl, g
            g -= tl.length
        tl = self.doc.timelines[-1]
        return tl, tl.length - 1

    @staticmethod
    def exportable(tl):
        """Indexes (top-to-bottom document order) of layers that produce artwork."""
        out = []
        for i, l in enumerate(tl.layers):
            if l.type in ("guide", "folder", "mask") or not l.visible:
                continue
            out.append(i)
        return out

    def slot_names(self):
        names = []
        for tl in self.doc.timelines:
            idx = list(reversed(self.exportable(tl)))
            for k, i in enumerate(idx):
                if k >= len(names):
                    names.append(tl.layers[i].name)
        return names or ["Layer 1"]

    def render_layers(self, g):
        """Bottom-to-top list of (name, ops tuple) for global frame g."""
        tl, t = self.locate(g)
        res = []
        for i in reversed(self.exportable(tl)):
            res.append((tl.layers[i].name, self._layer_with_mask(tl, i, t, IDENT, (), 0)))
        return res

    def frame_ops(self, g):
        tl, t = self.locate(g)
        return self.timeline_ops(tl, t, IDENT, (), 0)

    # ----- evaluation -----------------------------------------------------------
    def timeline_ops(self, tl, t, m, fx, depth):
        ops = []
        for i in reversed(range(len(tl.layers))):
            l = tl.layers[i]
            if l.type in ("guide", "folder", "mask") or not l.visible:
                continue
            ops.extend(self._layer_with_mask(tl, i, t, m, fx, depth))
        return tuple(ops)

    def _layer_with_mask(self, tl, i, t, m, fx, depth):
        layer = tl.layers[i]
        ops = tuple(self.layer_ops(layer, t, m, fx, depth))
        p = layer.parent
        if p is not None and 0 <= p < len(tl.layers) and tl.layers[p].type == "mask":
            mask = tuple(self.layer_ops(tl.layers[p], t, m, fx, depth))
            return (Masked(mask, ops),) if ops else ()
        return ops

    def layer_ops(self, layer, t, m, fx, depth):
        fr = layer.frame_at(t)
        if fr is None:
            return []
        elems = list(fr.elements)
        if fr.tween == "motion" and elems:
            nxt = layer.frame_starting_at(fr.index + fr.duration)
            if nxt and nxt.elements and isinstance(elems[0], (Instance, Group)) \
                    and isinstance(nxt.elements[0], (Instance, Group)):
                p = _ease((t - fr.index) / float(fr.duration), fr.ease)
                a, b = elems[0], nxt.elements[0]
                new = dataclasses.replace(
                    a, matrix=interp_matrix(a.matrix, b.matrix, p, fr.rotate, fr.rotate_times))
                if isinstance(a, Instance) and a.fx and isinstance(b, Instance) and b.fx:
                    fa, fb = a.fx, b.fx
                    new = dataclasses.replace(new, fx=dataclasses.replace(
                        fa, alpha_mul=fa.alpha_mul + (fb.alpha_mul - fa.alpha_mul) * p))
                elems[0] = new
        elif fr.tween == "shape":
            self.doc.warn("Shape tweens are not interpolated; the first keyframe is held")
        ops = []
        for el in elems:
            ops.extend(self._element(el, fr, t, m, fx, depth))
        return ops

    def _element(self, el, fr, t, m, fx, depth):
        if depth > MAX_DEPTH:
            self.doc.warn("symbol nesting too deep (cycle?); truncated")
            return []
        if isinstance(el, Shape):
            return self._shape(el, m, fx)
        if isinstance(el, Prim):
            return self._prim(el, m, fx)
        if isinstance(el, Group):
            gm = mat_mul(m, el.matrix)
            out = []
            for mem in el.members:
                out.extend(self._element(mem, fr, t, gm, fx, depth))
            return out
        if isinstance(el, TextBox):
            x, y = mat_apply(mat_mul(m, el.matrix), el.left + 2, el.top + 2)
            return [Text(el.text, _r(x), _r(y), _r(el.size * mat_scale(mat_mul(m, el.matrix))),
                         apply_fx(el.color, fx))]
        if isinstance(el, Instance):
            return self._instance(el, fr, t, m, fx, depth)
        return []

    def _instance(self, el, fr, t, m, fx, depth):
        im = mat_mul(m, el.matrix)
        nfx = ((el.fx,) + tuple(fx)) if el.fx else tuple(fx)
        if el.kind == "bitmap":
            img = self.bitmap(el.name)
            if img is None:
                return []
            alpha = apply_fx((0, 0, 0, 255), nfx)[3] / 255.0
            return [Img(el.name, tuple(_r(v) for v in im), _r(alpha), img)]
        sym = self.doc.symbols.get(el.name)
        if sym is None:
            self.doc.warn(f"missing library symbol: {el.name}")
            return []
        stype, tl = sym
        n = tl.length
        off = t - fr.index
        kind = (el.symbol_type or stype or "graphic").lower()
        if kind == "graphic":
            if el.loop == "single frame":
                inner = el.first_frame
            elif el.loop == "play once":
                inner = min(el.first_frame + off, n - 1)
            else:
                inner = (el.first_frame + off) % n
        elif kind == "button":
            inner = 0
        else:  # movie clip: free-running from the keyframe it appears on
            inner = off % n
        return list(self.timeline_ops(tl, inner, im, nfx, depth + 1))

    def _shape(self, sh, m, fx):
        ops = []
        for idx in sorted(sh.fills):
            segs = []
            for e in sh.edges:
                if e.fill1 == idx:
                    segs.extend(e.segs)
                if e.fill0 == idx:
                    segs.extend(_rev(s) for s in reversed(e.segs))
            if not segs:
                continue
            loops = tuple(_loop_points(l, m) for l in chain_loops(segs))
            loops = tuple(l for l in loops if len(l) >= 3)
            if loops:
                ops.append(Fill(loops, apply_fx(sh.fills[idx].flat_rgba(), fx)))
        sc = mat_scale(m)
        for e in sh.edges:
            st = sh.strokes.get(e.stroke) if e.stroke else None
            if st is None:
                continue
            pts = [mat_apply(m, e.segs[0].pts[0], e.segs[0].pts[1])]
            for s in e.segs:
                pts.extend(_seg_points(s, m))
            ops.append(Stroke(tuple((_r(x), _r(y)) for x, y in pts), _r(st.weight * sc),
                              apply_fx(st.paint.flat_rgba(), fx)))
        return ops

    def _prim(self, pr, m, fx):
        ops = []
        pts = tuple((_r(x), _r(y)) for x, y in (mat_apply(m, *p) for p in _ellipse_like(pr)))
        if pr.fill is not None:
            ops.append(Fill((pts,), apply_fx(pr.fill.flat_rgba(), fx)))
        if pr.stroke is not None:
            ops.append(Stroke(pts + (pts[0],), _r(pr.stroke.weight * mat_scale(m)),
                              apply_fx(pr.stroke.paint.flat_rgba(), fx)))
        return ops

    # ----- bitmaps --------------------------------------------------------------
    def bitmap(self, name):
        if name in self._bitmaps:
            return self._bitmaps[name]
        from PIL import Image
        img = None
        media = self.doc.media.get(name)
        cands = []
        if media:
            cands += ["LIBRARY/" + media.href, media.href, "bin/" + media.data_href]
        cands += ["LIBRARY/" + name, name]
        for c in cands:
            if not c or c.endswith("/"):
                continue
            data = self.doc.reader.read(c) if self.doc.reader else None
            if not data:
                continue
            try:
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                break
            except Exception:
                continue
        if img is None:
            self.doc.warn(f"bitmap '{name}' could not be decoded (Flash-internal bitmap data is "
                          "not supported); it was skipped")
        self._bitmaps[name] = img
        return img
