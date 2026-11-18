"""SVG serialisation of flattened ops (used for the svg and harmony targets)."""
from __future__ import annotations

import base64
import io
from xml.sax.saxutils import escape

from ..flatten import Fill, Img, Masked, Stroke, Text


def _col(rgba, white=False):
    r, g, b, a = rgba
    if white:
        r = g = b = 255
    return f"rgb({r},{g},{b})", round(a / 255.0, 4)


def _ops(ops, out, ids, white=False):
    for op in ops:
        if isinstance(op, Fill):
            d = " ".join("M" + " L".join(f"{x:g} {y:g}" for x, y in loop) + " Z" for loop in op.loops)
            c, a = _col(op.rgba, white)
            out.append(f'<path d="{d}" fill="{c}" fill-opacity="{a}" fill-rule="evenodd"/>')
        elif isinstance(op, Stroke):
            c, a = _col(op.rgba, white)
            pts = " ".join(f"{x:g},{y:g}" for x, y in op.points)
            out.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-opacity="{a}" '
                       f'stroke-width="{max(op.width, 0.5):g}" stroke-linecap="round" stroke-linejoin="round"/>')
        elif isinstance(op, Img):
            if op.image is None:
                continue
            buf = io.BytesIO()
            op.image.save(buf, "PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            m = " ".join(f"{v:g}" for v in op.matrix)
            out.append(f'<image width="{op.image.width}" height="{op.image.height}" opacity="{op.alpha:g}" '
                       f'transform="matrix({m})" href="data:image/png;base64,{b64}"/>')
        elif isinstance(op, Text):
            c, a = _col(op.rgba, white)
            lines = op.text.split("\n")
            for i, line in enumerate(lines):
                out.append(f'<text x="{op.x:g}" y="{op.y + op.size * (i + 1):g}" font-size="{op.size:g}" '
                           f'font-family="sans-serif" fill="{c}" fill-opacity="{a}">{escape(line)}</text>')
        elif isinstance(op, Masked):
            ids[0] += 1
            mid = f"m{ids[0]}"
            out.append(f'<mask id="{mid}" maskUnits="userSpaceOnUse" x="0" y="0" width="100000" height="100000">')
            _ops(op.mask, out, ids, white=True)
            out.append("</mask>")
            out.append(f'<g mask="url(#{mid})">')
            _ops(op.ops, out, ids)
            out.append("</g>")


def ops_to_svg(ops, width, height, bg=None):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" '
           f'viewBox="0 0 {width:g} {height:g}">']
    if bg:
        out.append(f'<rect width="100%" height="100%" fill="rgb({bg[0]},{bg[1]},{bg[2]})"/>')
    _ops(ops, out, [0])
    out.append("</svg>")
    return "\n".join(out)
