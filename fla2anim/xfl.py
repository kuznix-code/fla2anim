"""Reader for XFL-based Flash projects (CS5+ .fla zip archives, .xfl folders)."""
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

from .model import (
    IDENT, ColorFx, Document, Edge, Frame, Group, Instance, Layer, Media, Paint,
    Prim, Seg, Shape, StrokeStyle, TextBox, Timeline, UnsupportedFormatError, FlaError,
)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_LOCAL_MAGIC = b"PK\x03\x04"
ZIP_EMPTY_MAGIC = b"PK\x05\x06"


class DirSource:
    def __init__(self, root):
        self.root = Path(root)

    def read(self, name):
        p = self.root / name
        try:
            return p.read_bytes() if p.is_file() else None
        except OSError:
            return None


def _recover_entries(data):
    """Rebuild {name: bytes} by walking local file headers.

    Used when the zip central directory is missing or damaged (truncated or
    interrupted copies, trailing junk). Entries that cannot be fully
    decompressed are skipped.
    """
    files = {}
    pos = data.find(ZIP_LOCAL_MAGIC)
    while pos != -1 and pos + 30 <= len(data):
        (_sig, _ver, _flags, method, _t, _d, _crc,
         csize, _usize, nlen, elen) = struct.unpack("<4sHHHHHIIIHH", data[pos:pos + 30])
        raw = data[pos + 30:pos + 30 + nlen]
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError:
            name = raw.decode("cp437", "replace")
        start = pos + 30 + nlen + elen
        consumed = 0
        out = None
        if method == 8:
            d = zlib.decompressobj(-15)
            try:
                out = d.decompress(data[start:])
                if d.eof:
                    consumed = len(data) - start - len(d.unused_data)
                else:
                    out = None  # truncated stream: do not trust partial data
            except zlib.error:
                out = None
        elif method == 0:
            if csize:
                out = data[start:start + csize]
                consumed = csize
            else:  # sizes live in a data descriptor: run to the next header
                nxt = data.find(ZIP_LOCAL_MAGIC, start)
                end = nxt if nxt != -1 else len(data)
                out = data[start:end]
                consumed = end - start
        if out is not None and name and not name.endswith("/"):
            files[name.replace("\\", "/")] = out
        pos = data.find(ZIP_LOCAL_MAGIC, start + consumed)
    return files


class ZipSource:
    def __init__(self, path):
        self.recovered = False
        self.zf = None
        self._files = None
        try:
            self.zf = zipfile.ZipFile(path)
            self.names = {n.replace("\\", "/"): n for n in self.zf.namelist()}
        except zipfile.BadZipFile:
            # Central directory missing/damaged: recover from local headers.
            self._files = _recover_entries(Path(path).read_bytes())
            if not any(n.lower().endswith(".xml") for n in self._files):
                raise FlaError(
                    f"{Path(path).name}: the zip archive is damaged and nothing usable could be "
                    "recovered. It may be a truncated or interrupted copy; try copying it again, "
                    "or re-save it from Adobe Animate as an uncompressed .xfl.") from None
            self.names = {n: n for n in self._files}
            self.recovered = True

    def read(self, name):
        real = self.names.get(name)
        if not real:
            return None
        if self._files is not None:
            return self._files.get(real)
        try:
            return self.zf.read(real)
        except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError):
            return None  # corrupt entry: treated like a missing one


def open_source(path):
    p = Path(path)
    if not p.exists():
        raise FlaError(f"input not found: {p}")
    if p.is_dir():
        return DirSource(p)
    with p.open("rb") as f:
        head = f.read(8)
    if head == OLE_MAGIC:
        raise UnsupportedFormatError(
            "This is a legacy binary .fla (Flash 5 - CS4, including Flash 8). That format is "
            "proprietary and undocumented, so fla2anim cannot read it. Open it in Adobe "
            "Animate/Flash CS5+ and use 'Save as' -> 'Flash CS5+ Uncompressed Document "
            "(*.xfl)' or a CS5+ .fla, then convert that file."
        )
    if head[:4] in (ZIP_LOCAL_MAGIC, ZIP_EMPTY_MAGIC) or zipfile.is_zipfile(p):
        return ZipSource(p)
    if p.suffix.lower() == ".xfl":
        return DirSource(p.parent)  # .xfl marker file next to DOMDocument.xml
    raise FlaError(f"{p.name}: not a recognised Flash project (expected a CS5+ .fla zip or .xfl folder)")


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _parse_xml(data, what):
    try:
        return _strip_ns(ET.fromstring(data))
    except ET.ParseError as e:
        raise FlaError(f"cannot parse {what}: {e}") from e


def _float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def parse_color(s, alpha=1.0):
    s = (s or "#000000").lstrip("#")
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        r = g = b = 0
    a = max(0, min(255, int(round(_float(alpha, 1.0) * 255))))
    return (r, g, b, a)


def parse_matrix(el):
    m = el.find("matrix/Matrix") if el is not None else None
    if m is None:
        return IDENT
    g = m.get
    return (_float(g("a"), 1.0), _float(g("b"), 0.0), _float(g("c"), 0.0),
            _float(g("d"), 1.0), _float(g("tx"), 0.0), _float(g("ty"), 0.0))


def parse_paint(el):
    for c in el:
        if c.tag == "SolidColor":
            return Paint("solid", parse_color(c.get("color"), c.get("alpha", 1)))
        if c.tag in ("LinearGradient", "RadialGradient"):
            stops = []
            for ge in c.findall("GradientEntry"):
                stops.append((_float(ge.get("ratio")), parse_color(ge.get("color"), ge.get("alpha", 1))))
            return Paint("gradient", stops[0][1] if stops else (0, 0, 0, 255), stops)
        if c.tag == "BitmapFill":
            return Paint("bitmap", bitmap=c.get("bitmapPath"))
    return Paint()


_TOK = re.compile(r"[!|/\[\]]|S\d+|#[0-9A-Fa-f]+(?:\.[0-9A-Fa-f]+)?|-?\d+(?:\.\d+)?")
_CMDS = {"!", "|", "/", "[", "]"}


def _num(tok):
    if tok[0] == "#":
        ip, _, fp = tok[1:].partition(".")
        v = int(ip, 16)
        if v >= 0x80000000:
            v -= 1 << 32
        return v + (int(fp, 16) / (16 ** len(fp)) if fp else 0.0)
    return float(tok)


def parse_edge_path(s):
    """Parse an XFL `edges` attribute (twips) into pixel-space segments."""
    segs, cur, cmd, buf = [], (0.0, 0.0), None, []
    for t in _TOK.findall(s or ""):
        if t in _CMDS:
            cmd, buf = t, []
            continue
        if t[0] == "S" or cmd is None:
            continue
        buf.append(_num(t) / 20.0)
        need = 2 if cmd in "!|/" else 4
        if len(buf) == need:
            if cmd == "!":
                cur = (buf[0], buf[1])
            elif cmd in "|/":
                segs.append(Seg("L", (cur[0], cur[1], buf[0], buf[1])))
                cur = (buf[0], buf[1])
            else:
                segs.append(Seg("Q", (cur[0], cur[1], buf[0], buf[1], buf[2], buf[3])))
                cur = (buf[2], buf[3])
            buf = []
    return segs


def parse_shape(el):
    fills, strokes = {}, {}
    for fs in el.findall("fills/FillStyle"):
        fills[_int(fs.get("index"))] = parse_paint(fs)
    for ss in el.findall("strokes/StrokeStyle"):
        inner = next(iter(ss), None)
        if inner is None:
            continue
        fe = inner.find("fill")
        strokes[_int(ss.get("index"))] = StrokeStyle(
            _float(inner.get("weight"), 1.0), parse_paint(fe) if fe is not None else Paint())
    edges = []
    for e in el.findall("edges/Edge"):
        segs = parse_edge_path(e.get("edges"))
        if segs:
            edges.append(Edge(segs, _int(e.get("fillStyle0")), _int(e.get("fillStyle1")),
                              _int(e.get("strokeStyle"))))
    return Shape(fills, strokes, edges)


def parse_prim(el, kind):
    g = el.get
    fill = stroke = None
    fe = el.find("fill")
    if fe is not None:
        p = parse_paint(fe)
        fill = p if len(fe) else None
    se = el.find("stroke")
    if se is not None and len(se):
        inner = next(iter(se))
        fe2 = inner.find("fill")
        stroke = StrokeStyle(_float(inner.get("weight"), 1.0),
                             parse_paint(fe2) if fe2 is not None else Paint())
    radii = (_float(g("topLeftRadius")), _float(g("topRightRadius")),
             _float(g("bottomRightRadius")), _float(g("bottomLeftRadius")))
    return Prim(kind, _float(g("x")), _float(g("y")), _float(g("objectWidth")),
                _float(g("objectHeight")), radii, fill, stroke)


def parse_fx(el):
    c = el.find("color/Color")
    if c is None:
        return None
    g = c.get
    fx = ColorFx(
        alpha_mul=_float(g("alphaMultiplier"), 1.0), alpha_off=_float(g("alphaOffset")),
        r_mul=_float(g("redMultiplier"), 1.0), g_mul=_float(g("greenMultiplier"), 1.0),
        b_mul=_float(g("blueMultiplier"), 1.0), r_off=_float(g("redOffset")),
        g_off=_float(g("greenOffset")), b_off=_float(g("blueOffset")),
        brightness=_float(g("brightness")))
    if g("tintColor"):
        fx.tint = parse_color(g("tintColor"))[:3]
        fx.tint_mul = _float(g("tintMultiplier"))
    return fx


def parse_elements(parent, doc):
    out = []
    if parent is None:
        return out
    for el in parent:
        t = el.tag
        if t == "DOMShape":
            out.append(parse_shape(el))
        elif t == "DOMRectangleObject":
            out.append(_wrap_prim(el, "rect"))
        elif t == "DOMOvalObject":
            out.append(_wrap_prim(el, "oval"))
        elif t == "DOMSymbolInstance":
            out.append(Instance("symbol", el.get("libraryItemName", ""),
                                el.get("symbolType", "graphic"), parse_matrix(el),
                                el.get("loop", "loop"), _int(el.get("firstFrame")), parse_fx(el)))
        elif t == "DOMBitmapInstance":
            out.append(Instance("bitmap", el.get("libraryItemName", ""), "bitmap",
                                parse_matrix(el), fx=parse_fx(el)))
        elif t == "DOMGroup":
            out.append(Group(parse_matrix(el), parse_elements(el.find("members"), doc)))
        elif t in ("DOMStaticText", "DOMDynamicText", "DOMInputText"):
            out.append(_parse_text(el))
        elif t in ("DOMVideoInstance", "DOMCompiledClipInstance", "DOMComponentInstance"):
            doc.warn(f"unsupported element <{t}> was skipped")
        else:
            doc.warn(f"unknown element <{t}> was skipped")
    return [o for o in out if o is not None]


def _wrap_prim(el, kind):
    prim = parse_prim(el, kind)
    return Group(parse_matrix(el), [prim])


def _parse_text(el):
    chars = "".join((c.text or "") for c in el.iter("characters"))
    attrs = el.find(".//DOMTextAttrs")
    size = _float(attrs.get("size"), 12.0) if attrs is not None else 12.0
    col = parse_color(attrs.get("fillColor"), attrs.get("alpha", 1)) if attrs is not None else (0, 0, 0, 255)
    return TextBox(chars.replace("\r", "\n"), size, col, parse_matrix(el),
                   _float(el.get("left")), _float(el.get("top")))


def parse_timeline(el, doc):
    layers = []
    for le in el.findall("layers/DOMLayer"):
        par = le.get("parentLayerIndex")
        layer = Layer(le.get("name", "Layer"), le.get("layerType", "normal"),
                      le.get("visible", "true").lower() != "false",
                      _int(par) if par is not None else None)
        for fe in le.findall("frames/DOMFrame"):
            fr = Frame(_int(fe.get("index")), max(1, _int(fe.get("duration"), 1)))
            tw = fe.get("tweenType")
            fr.tween = tw if tw in ("motion", "shape") else None
            fr.ease = _float(fe.get("acceleration"))
            fr.rotate = fe.get("motionTweenRotate", "none")
            fr.rotate_times = _int(fe.get("motionTweenRotateTimes"))
            fr.elements = parse_elements(fe.find("elements"), doc)
            if fe.find("Motion") is not None or fe.find("motionObjectXML") is not None:
                doc.warn("CS4+ Motion Tweens are not interpolated; the first keyframe is held")
            layer.frames.append(fr)
        layer.frames.sort(key=lambda f: f.index)
        layers.append(layer)
    return Timeline(el.get("name", "Scene"), layers)


def load(path) -> Document:
    src = open_source(path)
    raw = src.read("DOMDocument.xml")
    if raw is None:
        raise FlaError("DOMDocument.xml not found - this does not look like an XFL project")
    root = _parse_xml(raw, "DOMDocument.xml")
    doc = Document(reader=src)
    if getattr(src, "recovered", False):
        doc.warn("the zip directory was damaged; files were recovered from local headers "
                 "and some assets may be missing")
    doc.width = _float(root.get("width"), 550.0)
    doc.height = _float(root.get("height"), 400.0)
    doc.fps = _float(root.get("frameRate"), 24.0) or 24.0
    doc.bg = parse_color(root.get("backgroundColor", "#FFFFFF"))[:3]
    for m in root.findall("media/*"):
        doc.media[m.get("name", "")] = Media(
            m.get("name", ""), m.get("href", ""), m.get("bitmapDataHRef", ""),
            m.get("sourceExternalFilepath", ""))
    for inc in root.findall("symbols/Include"):
        href = inc.get("href")
        data = src.read("LIBRARY/" + href) if href else None
        if data is None:
            doc.warn(f"library symbol file missing: {href}")
            continue
        sroot = _parse_xml(data, href)
        tl = sroot.find("timeline/DOMTimeline")
        if tl is not None:
            doc.symbols[sroot.get("name", href)] = (sroot.get("symbolType", "graphic"),
                                                    parse_timeline(tl, doc))
    for tl in root.findall("timelines/DOMTimeline"):
        doc.timelines.append(parse_timeline(tl, doc))
    if not doc.timelines:
        raise FlaError("project contains no timelines")
    return doc
