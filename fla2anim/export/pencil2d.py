"""Pencil2D (.pclx) exporter: one bitmap layer per Flash layer, rasterised keyframes."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

from PIL import Image

from ..flatten import Evaluator
from ..raster import render_ops
from . import Options


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def export_pencil2d(doc, out, opts: Options, ev=None):
    ev = ev or Evaluator(doc)
    out = Path(out)
    if out.suffix.lower() != ".pclx":
        out = out.with_suffix(".pclx")
    out.parent.mkdir(parents=True, exist_ok=True)
    a, b = opts.frame_range(doc)
    n = b - a
    W, H = max(1, round(doc.width * opts.scale)), max(1, round(doc.height * opts.scale))
    names = ev.slot_names()
    keyframes = [[] for _ in names]  # per slot: [(frame_no, png_bytes, left, top)]
    last = [None] * len(names)
    cache = {}
    for g in range(a, b):
        layers = ev.render_layers(g)
        for i in range(len(names)):
            ops = layers[i][1] if i < len(layers) else ()
            if ops == last[i] or (not ops and last[i] is None):
                continue
            last[i] = ops if ops else ()
            hit = cache.get(ops)
            if hit is None:
                img = render_ops(ops, doc.width, doc.height, opts.scale, None, opts.supersample)
                bb = img.getbbox()
                if bb is None:
                    hit = (_png(Image.new("RGBA", (1, 1), (0, 0, 0, 0))), 0, 0)
                else:
                    hit = (_png(img.crop(bb)), bb[0], bb[1])
                cache[ops] = hit
            keyframes[i].append((g - a + 1, hit[0], hit[1] - W // 2, hit[2] - H // 2))
        opts.tick(g - a + 1, n)

    layer_xml, files = [], {}
    lid = 0
    if opts.background:
        lid += 1
        bgimg = Image.new("RGBA", (W, H), tuple(doc.bg) + (255,))
        fn = f"{lid:03d}.001.png"
        files[fn] = _png(bgimg)
        layer_xml.append(f'    <layer id="{lid}" name="Background" visibility="1" type="1">\n'
                         f'      <image frame="1" src="{fn}" topLeftX="{-(W // 2)}" topLeftY="{-(H // 2)}"/>\n'
                         f"    </layer>")
    for name, kfs in zip(names, keyframes):
        lid += 1
        items = []
        for frame_no, data, x, y in kfs:
            fn = f"{lid:03d}.{frame_no:03d}.png"
            files[fn] = data
            items.append(f'      <image frame="{frame_no}" src="{fn}" topLeftX="{x}" topLeftY="{y}"/>')
        layer_xml.append(f'    <layer id="{lid}" name={quoteattr(name)} visibility="1" type="1">\n'
                         + "\n".join(items) + ("\n" if items else "") + "    </layer>")
    lid += 1
    layer_xml.append(f'    <layer id="{lid}" name="Camera Layer" visibility="1" type="5" width="{W}" height="{H}">\n'
                     f'      <camera frame="1" r="0" s="1" dx="0" dy="0"/>\n    </layer>')
    xml = ("<!DOCTYPE PencilDocument>\n<document>\n  <projectdata>\n"
           f"    <fps>{int(round(doc.fps))}</fps>\n    <currentFrame>1</currentFrame>\n"
           f"    <currentLayer>{max(1, lid - 1)}</currentLayer>\n  </projectdata>\n  <object>\n"
           + "\n".join(layer_xml) + "\n  </object>\n</document>\n")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("main.xml", xml)
        for fn, data in files.items():
            z.writestr("data/" + fn, data)
    return [out]
