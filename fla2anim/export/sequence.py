"""Image-sequence exporters: png, svg, opentoonz (PNG sequence), harmony (PNG + SVG)."""
from __future__ import annotations

from pathlib import Path

from ..flatten import Evaluator
from ..raster import render_ops
from . import Options
from .svg import ops_to_svg


def _frames(doc, opts, ev, want_png, want_svg, outdir, stem, sep, pad):
    a, b = opts.frame_range(doc)
    written = []
    bg = doc.bg if opts.background else None
    for g in range(a, b):
        ops = ev.frame_ops(g)
        num = str(g - a + 1).zfill(pad)
        if want_png:
            p = outdir[0] / f"{stem}{sep}{num}.png"
            render_ops(ops, doc.width, doc.height, opts.scale, bg, opts.supersample).save(p)
            written.append(p)
        if want_svg:
            p = outdir[1] / f"{stem}{sep}{num}.svg"
            p.write_text(ops_to_svg(ops, doc.width, doc.height, bg), encoding="utf-8")
            written.append(p)
        opts.tick(g - a + 1, b - a)
    return written


def _stem(out):
    return Path(out).name or "animation"


def export_png(doc, out, opts: Options, ev=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    return _frames(doc, opts, ev or Evaluator(doc), True, False, (out, out), _stem(out), "_", 4)


def export_svg(doc, out, opts: Options, ev=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    return _frames(doc, opts, ev or Evaluator(doc), False, True, (out, out), _stem(out), "_", 4)


def export_opentoonz(doc, out, opts: Options, ev=None):
    """PNG sequence named name.0001.png - OpenToonz loads it as one level (File > Load Level)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    return _frames(doc, opts, ev or Evaluator(doc), True, False, (out, out), _stem(out), ".", 4)


def export_harmony(doc, out, opts: Options, ev=None):
    """png/ and svg/ sequences for File > Import > Images in Toon Boom Harmony."""
    out = Path(out)
    png, svg = out / "png", out / "svg"
    png.mkdir(parents=True, exist_ok=True)
    svg.mkdir(parents=True, exist_ok=True)
    return _frames(doc, opts, ev or Evaluator(doc), True, True, (png, svg), _stem(out), "-", 4)
