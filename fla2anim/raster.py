"""Pillow-based rasteriser for flattened draw ops."""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .flatten import Fill, Img, Masked, Stroke, Text
from .model import mat_inv


def render_ops(ops, width, height, scale=1.0, bg=None, ss=2):
    """Render ops to an RGBA image of round(width*scale) x round(height*scale)."""
    W, H = max(1, round(width * scale)), max(1, round(height * scale))
    ss = max(1, int(ss))
    canvas = Image.new("RGBA", (W * ss, H * ss), tuple(bg) + (255,) if bg else (0, 0, 0, 0))
    _draw(canvas, ops, scale * ss * (W / (width * scale)) if width * scale else 1.0)
    if ss > 1:
        canvas = canvas.convert("RGBa").resize((W, H), Image.BOX).convert("RGBA")
    return canvas


def _bbox(points, pad, size):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0 = max(0, int(math.floor(min(xs) - pad)))
    y0 = max(0, int(math.floor(min(ys) - pad)))
    x1 = min(size[0], int(math.ceil(max(xs) + pad)) + 1)
    y1 = min(size[1], int(math.ceil(max(ys) + pad)) + 1)
    return x0, y0, x1, y1


def _blend(canvas, x0, y0, rgba, mask):
    bw, bh = mask.size
    if rgba[3] < 255:
        a = rgba[3]
        mask = mask.point(lambda v: v * a // 255)
    layer = Image.new("RGBA", (bw, bh), tuple(rgba[:3]) + (0,))
    layer.putalpha(mask)
    region = canvas.crop((x0, y0, x0 + bw, y0 + bh))
    canvas.paste(Image.alpha_composite(region, layer), (x0, y0))


def _draw(canvas, ops, k):
    for op in ops:
        if isinstance(op, Fill):
            _fill(canvas, op, k)
        elif isinstance(op, Stroke):
            _stroke(canvas, op, k)
        elif isinstance(op, Img):
            _img(canvas, op, k)
        elif isinstance(op, Text):
            _text(canvas, op, k)
        elif isinstance(op, Masked):
            _masked(canvas, op, k)


def _fill(canvas, op, k):
    allp = [(x * k, y * k) for loop in op.loops for x, y in loop]
    if not allp:
        return
    x0, y0, x1, y1 = _bbox(allp, 1, canvas.size)
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return
    total = Image.new("1", (bw, bh), 0)
    for loop in op.loops:
        if len(loop) < 3:
            continue
        small = Image.new("1", (bw, bh), 0)
        ImageDraw.Draw(small).polygon([(x * k - x0, y * k - y0) for x, y in loop], fill=1)
        total = ImageChops.logical_xor(total, small)
    _blend(canvas, x0, y0, op.rgba, total.convert("L"))


def _stroke(canvas, op, k):
    if len(op.points) < 2:
        return
    w = max(op.width, 0.5) * k
    pts = [(x * k, y * k) for x, y in op.points]
    x0, y0, x1, y1 = _bbox(pts, w / 2 + 2, canvas.size)
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return
    mask = Image.new("L", (bw, bh), 0)
    d = ImageDraw.Draw(mask)
    local = [(x - x0, y - y0) for x, y in pts]
    iw = max(1, int(round(w)))
    d.line(local, fill=255, width=iw, joint="curve")
    if iw >= 3:
        r = w / 2
        for cx, cy in (local[0], local[-1]):
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    _blend(canvas, x0, y0, op.rgba, mask)


def _img(canvas, op, k):
    a, b, c, d, tx, ty = (v * k for v in op.matrix)
    inv = mat_inv((a, b, c, d, tx, ty))
    if inv is None or op.image is None:
        return
    data = (inv[0], inv[2], inv[4], inv[1], inv[3], inv[5])
    out = op.image.transform(canvas.size, Image.AFFINE, data, resample=Image.BICUBIC)
    if op.alpha < 1.0:
        al = out.getchannel("A").point(lambda v: int(v * op.alpha))
        out.putalpha(al)
    canvas.alpha_composite(out)


def _text(canvas, op, k):
    try:
        font = ImageFont.load_default(size=max(6, int(op.size * k)))
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    ImageDraw.Draw(canvas).multiline_text((op.x * k, op.y * k), op.text, font=font, fill=tuple(op.rgba))


def _masked(canvas, op, k):
    mask = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    _draw(mask, op.mask, k)
    content = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    _draw(content, op.ops, k)
    content.putalpha(ImageChops.multiply(content.getchannel("A"), mask.getchannel("A")))
    canvas.alpha_composite(content)
