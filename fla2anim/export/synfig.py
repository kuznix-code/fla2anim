"""Synfig Studio (.sif) exporter: native vector layers, one group per unchanged run of a Flash layer.

Curves are flattened to polylines. Bitmaps, text and masks are not exported (a warning is issued).
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

from ..flatten import Evaluator, Fill, Img, Masked, Stroke, Text
from . import Options

UNIT = 60.0  # pixels per Synfig unit
GAMMA = 2.2


def _f(v):
    return f"{v:.6f}"


def _color(rgba):
    r, g, b, a = rgba
    lin = lambda c: (c / 255.0) ** GAMMA
    return (f"<color><r>{_f(lin(r))}</r><g>{_f(lin(g))}</g><b>{_f(lin(b))}</b>"
            f"<a>{_f(a / 255.0)}</a></color>")


def _vec(x, y, w, h):
    return f"<vector><x>{_f((x - w / 2) / UNIT)}</x><y>{_f((h / 2 - y) / UNIT)}</y></vector>"


def _bline(points, closed, w, h):
    if closed and len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    z = "<vector><x>0.000000</x><y>0.000000</y></vector>"
    ents = []
    for x, y in points:
        ents.append(
            "<entry><composite type=\"bline_point\">"
            f"<point>{_vec(x, y, w, h)}</point><width><real value=\"1.000000\"/></width>"
            "<origin><real value=\"0.500000\"/></origin><split><bool value=\"true\"/></split>"
            f"<t1>{z}</t1><t2>{z}</t2><split_radius><bool value=\"true\"/></split_radius>"
            "<split_angle><bool value=\"true\"/></split_angle></composite></entry>")
    return f'<bline type="bline_point" loop="{"true" if closed else "false"}">' + "".join(ents) + "</bline>"


def _common(desc):
    return ('<param name="z_depth"><real value="0.000000"/></param>'
            '<param name="amount"><real value="1.000000"/></param>'
            '<param name="blend_method"><integer value="0" static="true"/></param>')


def _fill_layer(op, w, h, n):
    out = []
    for loop in op.loops:
        if len(loop) < 3:
            continue
        out.append(
            f'<layer type="region" active="true" exclude_from_rendering="false" version="0.1" desc="fill">'
            + _common("fill") + f'<param name="color">{_color(op.rgba)}</param>'
            '<param name="origin"><vector><x>0.000000</x><y>0.000000</y></vector></param>'
            '<param name="invert"><bool value="false"/></param><param name="antialias"><bool value="true"/></param>'
            '<param name="feather"><real value="0.000000"/></param><param name="blurtype"><integer value="1"/></param>'
            '<param name="winding_style"><integer value="1"/></param>'
            f'<param name="bline">{_bline(loop, True, w, h)}</param></layer>')
    return out


def _stroke_layer(op, w, h):
    if len(op.points) < 2:
        return []
    closed = op.points[0] == op.points[-1] and len(op.points) > 2
    return [
        '<layer type="outline" active="true" exclude_from_rendering="false" version="0.2" desc="stroke">'
        + _common("stroke") + f'<param name="color">{_color(op.rgba)}</param>'
        '<param name="origin"><vector><x>0.000000</x><y>0.000000</y></vector></param>'
        '<param name="invert"><bool value="false"/></param><param name="antialias"><bool value="true"/></param>'
        '<param name="feather"><real value="0.000000"/></param><param name="blurtype"><integer value="1"/></param>'
        f'<param name="width"><real value="{_f(max(op.width, 0.5) / UNIT)}"/></param>'
        '<param name="expand"><real value="0.000000"/></param>'
        '<param name="sharp_cusps"><bool value="true"/></param>'
        '<param name="round_tip[0]"><bool value="true"/></param><param name="round_tip[1]"><bool value="true"/></param>'
        '<param name="homogeneous_width"><bool value="true"/></param>'
        f'<param name="bline">{_bline(op.points, closed, w, h)}</param></layer>']


def _group(name, inner, runs, total, fps, w, h):
    """runs: (start, end_exclusive) visibility interval within [0,total)."""
    start, end = runs
    if start <= 0 and end >= total:
        amount = '<param name="amount"><real value="1.000000"/></param>'
    else:
        wps = []
        t = lambda fr: f"{fr / fps:.6f}s"
        if start > 0:
            wps.append((0, 0.0))
        wps.append((start, 1.0))
        if end < total:
            wps.append((end, 0.0))
        amount = ('<param name="amount"><animated type="real">' + "".join(
            f'<waypoint time="{t(fr)}" before="constant" after="constant"><real value="{_f(v)}"/></waypoint>'
            for fr, v in wps) + "</animated></param>")
    return (f'<layer type="group" active="true" exclude_from_rendering="false" version="0.3" desc={quoteattr(name)}>'
            '<param name="z_depth"><real value="0.000000"/></param>' + amount +
            '<param name="blend_method"><integer value="0" static="true"/></param>'
            '<param name="origin"><vector><x>0.000000</x><y>0.000000</y></vector></param>'
            '<param name="transformation"><composite type="transformation">'
            '<offset><vector><x>0.000000</x><y>0.000000</y></vector></offset>'
            '<angle><angle value="0.000000"/></angle><skew_angle><angle value="0.000000"/></skew_angle>'
            '<scale><vector><x>1.000000</x><y>1.000000</y></vector></scale></composite></param>'
            '<param name="canvas"><canvas>' + "".join(inner) + "</canvas></param></layer>")


def export_synfig(doc, out, opts: Options, ev=None):
    ev = ev or Evaluator(doc)
    out = Path(out)
    if out.suffix.lower() != ".sif":
        out = out.with_suffix(".sif")
    out.parent.mkdir(parents=True, exist_ok=True)
    a, b = opts.frame_range(doc)
    n = b - a
    W, H = doc.width, doc.height
    names = ev.slot_names()
    runs = [[] for _ in names]  # per slot: [start, end, ops]
    skipped = set()
    for g in range(a, b):
        layers = ev.render_layers(g)
        for i in range(len(names)):
            ops = layers[i][1] if i < len(layers) else ()
            cur = runs[i][-1] if runs[i] else None
            if ops and cur and cur[2] == ops and cur[1] == g - a:
                cur[1] = g - a + 1  # extend the contiguous run
            elif ops:
                runs[i].append([g - a, g - a + 1, ops])
            # empty frame: the previous run simply ends
        opts.tick(g - a + 1, n)

    layers_xml = []
    if opts.background:
        layers_xml.append(
            '<layer type="solid_color" active="true" exclude_from_rendering="false" version="0.1" desc="Background">'
            + _common("bg") + f'<param name="color">{_color(tuple(doc.bg) + (255,))}</param>'
            '<param name="invert"><bool value="false"/></param></layer>')
    for name, rs in zip(names, runs):  # slots are bottom -> top, matching Synfig order
        for k, (s, e, ops) in enumerate(rs):
            inner = []

            def walk(seq):
                for op in seq:
                    if isinstance(op, Fill):
                        inner.extend(_fill_layer(op, W, H, 0))
                    elif isinstance(op, Stroke):
                        inner.extend(_stroke_layer(op, W, H))
                    elif isinstance(op, Masked):
                        skipped.add("masks (masked artwork exported unmasked)")
                        walk(op.ops)
                    elif isinstance(op, Img):
                        skipped.add("bitmaps")
                    elif isinstance(op, Text):
                        skipped.add("text")
            walk(ops)
            if inner:
                layers_xml.append(_group(f"{name} [{s + 1}-{e}]", inner, (s, e), n, doc.fps, W, H))
    for s in sorted(skipped):
        doc.warn(f"Synfig export does not include {s}")

    half_w, half_h = W / 2 / UNIT, H / 2 / UNIT
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<canvas version="1.4" width="{int(round(W))}" height="{int(round(H))}" xres="2834.645669" '
           f'yres="2834.645669" gamma-r="{GAMMA}" gamma-g="{GAMMA}" gamma-b="{GAMMA}" '
           f'view-box="{_f(-half_w)} {_f(half_h)} {_f(half_w)} {_f(-half_h)}" antialias="1" fps="{doc.fps:g}" '
           f'begin-time="0s" end-time="{max(0, n - 1) / doc.fps:.6f}s" bgcolor="0.500000 0.500000 0.500000 1.000000">\n'
           f"<name>{Path(out).stem}</name>\n<description>Converted by fla2anim</description>\n"
           + "\n".join(layers_xml) + "\n</canvas>\n")
    out.write_text(xml, encoding="utf-8")
    return [out]
