"""Plain data model for a parsed XFL document, plus 2D affine helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# Affine matrix as a tuple (a, b, c, d, tx, ty):
#   x' = a*x + c*y + tx ;  y' = b*x + d*y + ty
IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_mul(m1, m2):
    """Return m1 o m2 (apply m2 first, then m1)."""
    a1, b1, c1, d1, tx1, ty1 = m1
    a2, b2, c2, d2, tx2, ty2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * tx2 + c1 * ty2 + tx1,
        b1 * tx2 + d1 * ty2 + ty1,
    )


def mat_apply(m, x, y):
    a, b, c, d, tx, ty = m
    return (a * x + c * y + tx, b * x + d * y + ty)


def mat_inv(m):
    a, b, c, d, tx, ty = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * tx + ic * ty), -(ib * tx + id_ * ty))


def mat_scale(m):
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


class FlaError(Exception):
    """Base error."""


class UnsupportedFormatError(FlaError):
    """The input is a valid Flash file but in a format we cannot read."""


@dataclass
class Paint:
    kind: str = "solid"  # solid | gradient | bitmap
    rgba: tuple = (0, 0, 0, 255)
    stops: list = field(default_factory=list)  # [(ratio 0..1, rgba)]
    bitmap: Optional[str] = None

    def flat_rgba(self):
        if self.kind == "gradient" and self.stops:
            n = len(self.stops)
            return tuple(int(round(sum(s[1][i] for s in self.stops) / n)) for i in range(4))
        if self.kind == "bitmap":
            return (128, 128, 128, 255)
        return self.rgba


@dataclass
class StrokeStyle:
    weight: float = 1.0
    paint: Paint = field(default_factory=Paint)


@dataclass
class Seg:
    kind: str  # 'L' (x0,y0,x1,y1) or 'Q' (x0,y0,cx,cy,x1,y1); pixel units
    pts: tuple


@dataclass
class Edge:
    segs: list
    fill0: int = 0  # fill on the left
    fill1: int = 0  # fill on the right
    stroke: int = 0


@dataclass
class Shape:
    fills: dict
    strokes: dict
    edges: list


@dataclass
class Prim:
    kind: str  # rect | oval
    x: float
    y: float
    w: float
    h: float
    radii: tuple = (0, 0, 0, 0)
    fill: Optional[Paint] = None
    stroke: Optional[StrokeStyle] = None


@dataclass
class ColorFx:
    alpha_mul: float = 1.0
    alpha_off: float = 0.0
    r_mul: float = 1.0
    g_mul: float = 1.0
    b_mul: float = 1.0
    r_off: float = 0.0
    g_off: float = 0.0
    b_off: float = 0.0
    tint: Optional[tuple] = None
    tint_mul: float = 0.0
    brightness: float = 0.0


@dataclass
class Instance:
    kind: str  # symbol | bitmap
    name: str
    symbol_type: str = "graphic"
    matrix: tuple = IDENT
    loop: str = "loop"
    first_frame: int = 0
    fx: Optional[ColorFx] = None


@dataclass
class Group:
    matrix: tuple
    members: list


@dataclass
class TextBox:
    text: str
    size: float
    color: tuple
    matrix: tuple
    left: float = 0.0
    top: float = 0.0


@dataclass
class Frame:
    index: int
    duration: int
    tween: Optional[str] = None
    ease: float = 0.0
    rotate: str = "none"
    rotate_times: int = 0
    elements: list = field(default_factory=list)


@dataclass
class Layer:
    name: str
    type: str = "normal"  # normal | guide | mask | folder
    visible: bool = True
    parent: Optional[int] = None
    frames: list = field(default_factory=list)

    def frame_at(self, t):
        for fr in self.frames:
            if fr.index <= t < fr.index + fr.duration:
                return fr
        return None

    def frame_starting_at(self, t):
        for fr in self.frames:
            if fr.index == t:
                return fr
        return None

    @property
    def length(self):
        return max((f.index + f.duration for f in self.frames), default=0)


@dataclass
class Timeline:
    name: str
    layers: list

    @property
    def length(self):
        return max((l.length for l in self.layers), default=1) or 1


@dataclass
class Media:
    name: str
    href: str = ""
    data_href: str = ""
    source: str = ""


@dataclass
class Document:
    width: float = 550.0
    height: float = 400.0
    fps: float = 24.0
    bg: tuple = (255, 255, 255)
    timelines: list = field(default_factory=list)
    symbols: dict = field(default_factory=dict)  # name -> (symbol_type, Timeline)
    media: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    reader: object = None  # Source used to load external assets

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    @property
    def total_frames(self):
        return sum(t.length for t in self.timelines) or 1
