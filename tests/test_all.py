import io
import json
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_fla, build_files  # noqa: E402

from fla2anim import xfl, cli  # noqa: E402
from fla2anim.export import Options  # noqa: E402
from fla2anim.export.pencil2d import export_pencil2d  # noqa: E402
from fla2anim.export.sequence import export_harmony, export_opentoonz, export_png, export_svg  # noqa: E402
from fla2anim.export.swf import export_swf  # noqa: E402
from fla2anim.export.synfig import export_synfig  # noqa: E402
from fla2anim.flatten import Evaluator, Fill, interp_matrix  # noqa: E402
from fla2anim.model import FlaError, UnsupportedFormatError, mat_inv, mat_mul  # noqa: E402
from fla2anim.raster import render_ops  # noqa: E402


@pytest.fixture()
def fla(tmp_path):
    return make_fla(tmp_path / "t.fla")


@pytest.fixture()
def doc(fla):
    return xfl.load(fla)


# ---------------------------------------------------------------- parsing
def test_edge_parsing_decimal_hex_and_selection_flags():
    segs = xfl.parse_edge_path("!200 400S1|600 400[800 600 1000 400/1000 0")
    assert [s.kind for s in segs] == ["L", "Q", "L"]
    assert segs[0].pts == (10.0, 20.0, 30.0, 20.0)
    assert segs[1].pts == (30.0, 20.0, 40.0, 30.0, 50.0, 20.0)
    hexseg = xfl.parse_edge_path("!#64.80 #0|#C8 0")  # 100.5 twips -> 5.025 px
    assert hexseg[0].pts[0] == pytest.approx(5.025)
    assert hexseg[0].pts[2] == pytest.approx(10.0)


def test_negative_hex_edge_value():
    seg = xfl.parse_edge_path("!#FFFFFF9C.00 0|0 0")[0]  # -100 twips
    assert seg.pts[0] == pytest.approx(-5.0)


def test_document_properties(doc):
    assert (doc.width, doc.height, doc.fps, doc.total_frames) == (200, 100, 12, 10)
    assert "Ball" in doc.symbols
    assert [l.type for l in doc.timelines[0].layers].count("mask") == 1


def test_xfl_folder_and_marker_file(tmp_path):
    for name, data in build_files().items():
        p = tmp_path / "proj" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
    (tmp_path / "proj" / "proj.xfl").write_text("PROXY-CS5")
    assert xfl.load(tmp_path / "proj").total_frames == 10
    assert xfl.load(tmp_path / "proj" / "proj.xfl").total_frames == 10


def test_binary_fla_is_rejected_clearly(tmp_path):
    p = tmp_path / "old.fla"
    p.write_bytes(xfl.OLE_MAGIC + b"\0" * 100)
    with pytest.raises(UnsupportedFormatError, match="legacy binary"):
        xfl.load(p)


def test_garbage_and_missing(tmp_path):
    g = tmp_path / "x.fla"
    g.write_bytes(b"hello")
    with pytest.raises(FlaError):
        xfl.load(g)
    with pytest.raises(FlaError):
        xfl.load(tmp_path / "nope.fla")
    z = tmp_path / "z.fla"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(FlaError, match="DOMDocument"):
        xfl.load(z)


# ---------------------------------------------------------------- evaluation
def ball_center(ops):
    red = [o for o in ops if isinstance(o, Fill) and o.rgba[:3] == (255, 0, 0)]
    assert len(red) == 1
    pts = red[0].loops[0]
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def test_motion_tween_interpolates(doc):
    ev = Evaluator(doc)
    xs = [ball_center(ev.frame_ops(g))[0] for g in range(10)]
    assert xs[0] == pytest.approx(30, abs=0.5)
    assert xs[1] == pytest.approx(58, abs=0.5)   # 30 + 140 * 1/5
    assert xs[2] == pytest.approx(86, abs=0.5)
    assert xs[4] == pytest.approx(142, abs=0.5)
    assert all(x == pytest.approx(170, abs=0.5) for x in xs[5:])  # holds after last tween frame


def test_interp_matrix_rotation_shortest_path():
    import math
    m0 = (1, 0, 0, 1, 0, 0)
    m1 = (math.cos(math.radians(350)), math.sin(math.radians(350)), -math.sin(math.radians(350)),
          math.cos(math.radians(350)), 0, 0)
    mid = interp_matrix(m0, m1, 0.5)
    assert math.degrees(math.atan2(mid[1], mid[0])) == pytest.approx(-5, abs=0.01)


def test_matrix_inverse_roundtrip():
    m = (2, 0.5, -0.3, 1.5, 10, -4)
    i = mat_inv(m)
    r = mat_mul(m, i)
    assert r == pytest.approx((1, 0, 0, 1, 0, 0), abs=1e-9)


def test_layer_order_and_names(doc):
    ev = Evaluator(doc)
    assert ev.slot_names() == ["bg", "masked", "mover", "lines"]


# ---------------------------------------------------------------- raster
def px(im, x, y):
    return im.getpixel((x, y))


def test_render_colours_and_mask(doc):
    ev = Evaluator(doc)
    im = render_ops(ev.frame_ops(0), doc.width, doc.height, 1.0, doc.bg)
    assert im.size == (200, 100)
    assert px(im, 100, 5)[:3] == (0xCC, 0xE5, 0xFF)      # background rect
    assert px(im, 30, 50)[:3] == (255, 0, 0)              # ball at start
    assert px(im, 110, 25)[:3] == (0, 0xAA, 0)            # inside mask circle
    assert px(im, 85, 25)[:3] == (0xCC, 0xE5, 0xFF)       # green square but outside mask
    assert px(im, 100, 90)[:3] == (0, 0, 255)             # stroke


def test_scale_and_transparency(doc):
    ev = Evaluator(doc)
    im = render_ops(ev.frame_ops(0), doc.width, doc.height, 2.0, None)
    assert im.size == (400, 200)
    assert px(im, 399, 1)[3] == 0 or px(im, 399, 1)[3] == 255


def test_even_odd_hole():
    from fla2anim.flatten import Fill as F
    outer = tuple((x, y) for x, y in [(0, 0), (20, 0), (20, 20), (0, 20)])
    hole = tuple((x, y) for x, y in [(5, 5), (15, 5), (15, 15), (5, 15)])
    im = render_ops([F((outer, hole), (255, 0, 0, 255))], 20, 20, 1.0, None)
    assert px(im, 2, 2) == (255, 0, 0, 255)
    assert px(im, 10, 10)[3] == 0


# ---------------------------------------------------------------- exporters
def test_pencil2d(doc, tmp_path):
    (out,) = export_pencil2d(doc, tmp_path / "a.pclx", Options())
    z = zipfile.ZipFile(out)
    assert z.testzip() is None
    xml = z.read("main.xml").decode()
    assert xml.startswith("<!DOCTYPE PencilDocument>")
    root = ET.fromstring(xml.split("\n", 1)[1])
    layers = root.findall("object/layer")
    assert [l.get("name") for l in layers] == ["bg", "masked", "mover", "lines", "Camera Layer"]
    assert layers[-1].get("type") == "5" and layers[-1].get("width") == "200"
    assert root.find("projectdata/fps").text == "12"
    mover = layers[2].findall("image")
    assert [i.get("frame") for i in mover] == ["1", "2", "3", "4", "5", "6"]  # tween 1-5, then held
    assert len(layers[0].findall("image")) == 1  # static background = single keyframe
    for im in layers[0].findall("image") + mover:
        data = z.read("data/" + im.get("src"))
        Image.open(io.BytesIO(data)).verify()
    assert layers[0].find("image").get("topLeftX") == str(0 - 200 // 2)


def test_pencil2d_background_and_range(doc, tmp_path):
    (out,) = export_pencil2d(doc, tmp_path / "b", Options(background=True, frames=(2, 4)))
    assert out.suffix == ".pclx"
    root = ET.fromstring(zipfile.ZipFile(out).read("main.xml").decode().split("\n", 1)[1])
    assert root.findall("object/layer")[0].get("name") == "Background"
    mover = [l for l in root.findall("object/layer") if l.get("name") == "mover"][0]
    assert [i.get("frame") for i in mover.findall("image")] == ["1", "2"]


def test_synfig_wellformed(doc, tmp_path):
    (out,) = export_synfig(doc, tmp_path / "a.sif", Options())
    root = ET.parse(out).getroot()
    assert root.tag == "canvas" and root.get("fps") == "12"
    assert root.get("end-time") == f"{9 / 12:.6f}s"
    groups = root.findall("layer[@type='group']")
    assert len(groups) >= 3
    # static layers have constant amount, the tweened one animates
    static = [g for g in groups if g.get("desc").startswith("bg")][0]
    assert static.find("param[@name='amount']/real").get("value") == "1.000000"
    regions = root.findall(".//layer[@type='region']")
    outlines = root.findall(".//layer[@type='outline']")
    assert regions and outlines
    wps = root.findall(".//param[@name='amount']/animated/waypoint")
    assert wps and all(w.get("before") == "constant" for w in wps)
    for pt in root.findall(".//bline[@type='bline_point']")[:1]:
        assert pt.find("entry/composite/point/vector/x") is not None


def decode_swf(data):
    """Independent mini SWF reader: returns header info and per-shape polygon areas."""
    assert data[:3] == b"FWS"
    version = data[3]
    (length,) = struct.unpack("<I", data[4:8])
    assert length == len(data)

    def read_rect(buf, pos):
        nb = buf[pos] >> 3
        total = 5 + 4 * nb
        bits = int.from_bytes(buf[pos:pos + (total + 7) // 8], "big") >> ((total + 7) // 8 * 8 - total)
        vals, shift = [], total - 5
        for _ in range(4):
            shift -= nb
            v = (bits >> shift) & ((1 << nb) - 1)
            if v >> (nb - 1):
                v -= 1 << nb
            vals.append(v)
        return vals, pos + (total + 7) // 8

    frame_rect, pos = read_rect(data, 8)
    fr, frames = struct.unpack("<HH", data[pos:pos + 4])
    pos += 4
    tags = []
    while pos < len(data):
        (h,) = struct.unpack("<H", data[pos:pos + 2])
        pos += 2
        code, ln = h >> 6, h & 63
        if ln == 63:
            (ln,) = struct.unpack("<I", data[pos:pos + 4])
            pos += 4
        tags.append((code, data[pos:pos + ln]))
        pos += ln
        if code == 0:
            break
    assert pos == len(data)
    return version, frame_rect, fr, frames, tags


class BitReader:
    def __init__(self, b, pos=0):
        self.b, self.p = b, pos * 8

    def ub(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.b[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def sb(self, n):
        v = self.ub(n)
        return v - (1 << n) if n and v >> (n - 1) else v


def shape_polygons(body):
    """Decode a DefineShape3 body (after id): returns (fill_colors, polylines by fill idx)."""
    nb = body[0] >> 3
    pos = (5 + 4 * nb + 7) // 8
    n = body[pos]
    pos += 1
    fills = []
    for _ in range(n):
        assert body[pos] == 0
        fills.append(tuple(body[pos + 1:pos + 5]))
        pos += 5
    n = body[pos]
    pos += 1
    lines = []
    for _ in range(n):
        w = struct.unpack("<H", body[pos:pos + 2])[0]
        lines.append((w, tuple(body[pos + 2:pos + 6])))
        pos += 6
    r = BitReader(body, pos)
    nf, nl = r.ub(4), r.ub(4)
    x = y = 0
    paths, cur, cur_style = [], None, None
    while True:
        if r.ub(1) == 0:
            flags = r.ub(5)
            if flags == 0:
                break
            assert flags & 0b00001, "expected move"
            mb = r.ub(5)
            x, y = r.sb(mb), r.sb(mb)
            f1 = r.ub(nf) if flags & 0b00100 else None
            ln = r.ub(nl) if flags & 0b00010 else None
            cur = [(x, y)]
            paths.append(((f1, ln), cur))
        else:
            assert r.ub(1) == 1
            nbits = r.ub(4) + 2
            assert r.ub(1) == 1
            x, y = x + r.sb(nbits), y + r.sb(nbits)
            cur.append((x, y))
    return fills, lines, paths


def area(pts):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2 / 400.0


def test_swf_structure_and_geometry(doc, tmp_path):
    (out,) = export_swf(doc, tmp_path / "a.swf", Options())
    version, frect, fr, frames, tags = decode_swf(out.read_bytes())
    assert version == 5 and frames == 10 and fr == 12 * 256
    assert frect == [0, 4000, 0, 2000]
    codes = [c for c, _ in tags]
    assert codes.count(1) == 10 and codes[-1] == 0 and codes[0] == 9
    assert tags[0][1] == bytes(doc.bg)
    defs = [b for c, b in tags if c == 32]
    assert len(defs) == 9  # bg + masked + lines + 6 distinct ball states (5 tween frames + held)
    # geometry round trip: first placed shape is 'bg' (200x100 rectangle = 20000 px^2)
    fills, lines, paths = shape_polygons(defs[0][2:])
    assert fills[0][:3] == (0xCC, 0xE5, 0xFF)
    assert area(paths[0][1][:-1] if paths[0][1][0] == paths[0][1][-1] else paths[0][1]) == pytest.approx(20000)
    # ball shape (red) polygon area ~ pi*20^2 (flattened quads: slightly smaller)
    for body in defs:
        f, l, p = shape_polygons(body[2:])
        if f and f[0][:3] == (255, 0, 0):
            a = area(p[0][1][:-1])
            ref = [o for o in Evaluator(doc).frame_ops(0) if isinstance(o, Fill) and o.rgba[:3] == (255, 0, 0)][0]
            loop = list(ref.loops[0])
            ref_area = abs(sum(u[0] * v[1] - v[0] * u[1] for u, v in zip(loop, loop[1:] + loop[:1]))) / 2
            assert a == pytest.approx(ref_area, abs=1.0)  # twip rounding only
            assert 1240 < a < 1270                         # ~ pi * 20^2
            break
    else:
        pytest.fail("no ball shape found")


def test_swf_place_and_remove_balance(doc, tmp_path):
    (out,) = export_swf(doc, tmp_path / "a.swf", Options(swf_version=8))
    version, _, _, _, tags = decode_swf(out.read_bytes())
    assert version == 8
    depth = {}
    for c, b in tags:
        if c == 26:
            d = struct.unpack("<H", b[1:3])[0]
            assert d not in depth, "depth placed twice without removal"
            depth[d] = True
        elif c == 28:
            d = struct.unpack("<H", b)[0]
            assert depth.pop(d), "removed an empty depth"


def test_swf_rejects_old_version(doc, tmp_path):
    with pytest.raises(ValueError):
        export_swf(doc, tmp_path / "a.swf", Options(swf_version=2))


def test_sequences(doc, tmp_path):
    ev = Evaluator(doc)
    files = export_png(doc, tmp_path / "seq", Options(), ev)
    assert [f.name for f in files][:2] == ["seq_0001.png", "seq_0002.png"] and len(files) == 10
    assert Image.open(files[0]).size == (200, 100)
    ot = export_opentoonz(doc, tmp_path / "ot", Options(scale=0.5), ev)
    assert ot[0].name == "ot.0001.png" and Image.open(ot[0]).size == (100, 50)
    hm = export_harmony(doc, tmp_path / "hm", Options(frames=(1, 3)), ev)
    assert sorted(p.suffix for p in hm) == [".png", ".png", ".svg", ".svg"]
    sv = export_svg(doc, tmp_path / "sv", Options(), ev)
    root = ET.parse(sv[0]).getroot()
    assert root.tag.endswith("svg") and root.get("width") == "200"
    text = sv[0].read_text()
    assert "<mask" in text and 'fill-rule="evenodd"' in text


def test_transparent_png_by_default_and_background_option(doc, tmp_path):
    a = export_png(doc, tmp_path / "x", Options(frames=(1, 1)))[0]
    im = Image.open(a)
    assert im.getpixel((199, 99))[3] in (0, 255)
    b = export_png(doc, tmp_path / "y", Options(frames=(1, 1), background=True))[0]
    assert Image.open(b).getpixel((0, 0))[:3] != (0, 0, 0)


# ---------------------------------------------------------------- CLI
def test_cli_roundtrip(fla, tmp_path, capsys):
    out = tmp_path / "o.pclx"
    assert cli.main([str(fla), "-t", "pencil2d", "-o", str(out), "-q"]) == 0
    assert out.exists()
    assert cli.main([str(fla), "--info"]) == 0
    assert "200x100" in capsys.readouterr().out


def test_cli_errors(tmp_path, capsys):
    assert cli.main([]) == 2
    assert cli.main([str(tmp_path / "missing.fla"), "-t", "png"]) == 1
    old = tmp_path / "old.fla"
    old.write_bytes(xfl.OLE_MAGIC + b"\0" * 64)
    assert cli.main([str(old), "-t", "png"]) == 1
    assert "legacy binary" in capsys.readouterr().err
    f = make_fla(tmp_path / "t.fla")
    assert cli.main([str(f)]) == 2
    assert cli.main([str(f), "-t", "swf", "--swf-version", "1", "-q"]) == 2
    assert cli.main([str(f), "-t", "png", "--scale", "0"]) == 2


def test_cli_frame_range(fla, tmp_path):
    out = tmp_path / "r"
    assert cli.main([str(fla), "-t", "png", "-o", str(out), "--frames", "3:5", "-q"]) == 0
    assert len(list(out.glob("*.png"))) == 3


# ---------------------------------------------------------------- extra semantics
def _doc_from_layers(tmp_path, layers_xml, frames=1, extra_doc=""):
    from fixtures import NS
    xml = (f'<DOMDocument {NS} width="200" height="100" frameRate="12">{extra_doc}'
           f'<timelines><DOMTimeline name="Scene 1"><layers>{layers_xml}</layers></DOMTimeline></timelines></DOMDocument>')
    p = tmp_path / "c.fla"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("DOMDocument.xml", xml)
    return xfl.load(p)


def test_adjacent_fills_share_an_edge_with_fillstyle0_and_1(tmp_path):
    from fixtures import frame, layer, tw
    T = tw
    # Left square (fill 1) and right square (fill 2) share the vertical edge x=100.
    # Going south along the shared edge: right-hand side is WEST (y-down) -> fill1 = left square.
    edges = (
        f'<Edge fillStyle1="1" edges="!{T(0)} {T(0)}|{T(100)} {T(0)}"/>'            # top-left, interior south = right
        f'<Edge fillStyle1="2" edges="!{T(100)} {T(0)}|{T(200)} {T(0)}"/>'          # top-right
        f'<Edge fillStyle1="2" edges="!{T(200)} {T(0)}|{T(200)} {T(100)}"/>'        # right side
        f'<Edge fillStyle1="2" edges="!{T(200)} {T(100)}|{T(100)} {T(100)}"/>'      # bottom-right
        f'<Edge fillStyle1="1" edges="!{T(100)} {T(100)}|{T(0)} {T(100)}"/>'        # bottom-left
        f'<Edge fillStyle1="1" edges="!{T(0)} {T(100)}|{T(0)} {T(0)}"/>'            # left side
        f'<Edge fillStyle0="2" fillStyle1="1" edges="!{T(100)} {T(0)}|{T(100)} {T(100)}"/>'  # shared
    )
    shp = ('<DOMShape><fills><FillStyle index="1"><SolidColor color="#FF0000"/></FillStyle>'
           '<FillStyle index="2"><SolidColor color="#0000FF"/></FillStyle></fills>'
           f'<edges>{edges}</edges></DOMShape>')
    doc = _doc_from_layers(tmp_path, layer("a", [frame(0, 1, shp)]))
    im = render_ops(Evaluator(doc).frame_ops(0), 200, 100, 1.0, None)
    assert px(im, 50, 50) == (255, 0, 0, 255)
    assert px(im, 150, 50) == (0, 0, 255, 255)
    assert px(im, 99, 50)[:3] == (255, 0, 0) and px(im, 101, 50)[:3] == (0, 0, 255)


def test_fillstyle0_only_shape(tmp_path):
    from fixtures import frame, layer, tw
    T = tw
    # counter-clockwise outline with the fill declared on the LEFT (fillStyle0)
    pts = [(10, 10), (10, 90), (190, 90), (190, 10)]
    edges = "".join(
        f'<Edge fillStyle0="1" edges="!{T(a[0])} {T(a[1])}|{T(b[0])} {T(b[1])}"/>'
        for a, b in zip(pts, pts[1:] + pts[:1]))
    shp = ('<DOMShape><fills><FillStyle index="1"><SolidColor color="#00FF00" alpha="0.5"/></FillStyle></fills>'
           f'<edges>{edges}</edges></DOMShape>')
    doc = _doc_from_layers(tmp_path, layer("a", [frame(0, 1, shp)]))
    im = render_ops(Evaluator(doc).frame_ops(0), 200, 100, 1.0, None)
    assert px(im, 100, 50)[:3] == (0, 255, 0) and abs(px(im, 100, 50)[3] - 128) <= 1
    assert px(im, 3, 3)[3] == 0


def test_multiple_scenes_are_concatenated(tmp_path):
    p = make_fla(tmp_path / "s.fla")
    z = zipfile.ZipFile(p)
    files = {n: z.read(n).decode() for n in z.namelist()}
    z.close()
    d = files["DOMDocument.xml"]
    start = d.index('<DOMTimeline name="Scene 1">')
    end = d.index("</timelines>")
    scene = d[start:end]
    files["DOMDocument.xml"] = d[:end] + scene.replace("Scene 1", "Scene 2") + d[end:]
    p2 = tmp_path / "s2.fla"
    with zipfile.ZipFile(p2, "w") as zf:
        for n, c in files.items():
            zf.writestr(n, c)
    doc = xfl.load(p2)
    assert doc.total_frames == 20 and len(doc.timelines) == 2
    ev = Evaluator(doc)
    assert ev.frame_ops(10) == ev.frame_ops(0)
    assert ev.frame_ops(19) == ev.frame_ops(9)


def test_alpha_and_tint_effects_and_nested_symbols(tmp_path):
    from fixtures import circle_edges, frame, layer, shape, NS
    inner = (f'<DOMSymbolItem {NS} name="Dot" symbolType="graphic"><timeline><DOMTimeline name="Dot"><layers>'
             + layer("l", [frame(0, 1, shape("#FF0000", circle_edges(0, 0, 10)))])
             + "</layers></DOMTimeline></timeline></DOMSymbolItem>")
    outer = (f'<DOMSymbolItem {NS} name="Wrap" symbolType="movie clip"><timeline><DOMTimeline name="Wrap"><layers>'
             + layer("l", [frame(0, 1, '<DOMSymbolInstance libraryItemName="Dot" symbolType="graphic">'
                                 '<matrix><Matrix tx="20" ty="20"/></matrix></DOMSymbolInstance>')])
             + "</layers></DOMTimeline></timeline></DOMSymbolItem>")
    inst = ('<DOMSymbolInstance libraryItemName="Wrap" symbolType="movie clip"><matrix><Matrix a="2" d="2" tx="10" ty="10"/></matrix>'
            '<color><Color alphaMultiplier="0.5"/></color></DOMSymbolInstance>')
    doc_xml = (f'<DOMDocument {NS} width="100" height="100" frameRate="12"><symbols><Include href="Dot.xml"/>'
               '<Include href="Wrap.xml"/></symbols><timelines><DOMTimeline name="S"><layers>'
               + layer("a", [frame(0, 1, inst)]) + "</layers></DOMTimeline></timelines></DOMDocument>")
    p = tmp_path / "n.fla"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("DOMDocument.xml", doc_xml)
        z.writestr("LIBRARY/Dot.xml", inner)
        z.writestr("LIBRARY/Wrap.xml", outer)
    doc = xfl.load(p)
    (op,) = [o for o in Evaluator(doc).frame_ops(0)]
    assert op.rgba == (255, 0, 0, 128)
    xs = [pt[0] for pt in op.loops[0]]
    cx = (min(xs) + max(xs)) / 2
    assert cx == pytest.approx(10 + 2 * 20, abs=0.2)           # tx 10 + scale 2 * inner offset 20
    assert (max(xs) - min(xs)) == pytest.approx(40, abs=0.5)   # radius 10 scaled x2 -> diameter 40


def test_missing_symbol_and_unsupported_elements_warn_not_crash(tmp_path):
    from fixtures import frame, layer
    els = ('<DOMSymbolInstance libraryItemName="Nope" symbolType="graphic"/>'
           '<DOMVideoInstance libraryItemName="v"/><DOMBitmapInstance libraryItemName="x.png"/>')
    doc = _doc_from_layers(tmp_path, layer("a", [frame(0, 1, els)]))
    ev = Evaluator(doc)
    assert ev.frame_ops(0) == ()
    assert any("Nope" in w for w in doc.warnings) and any("DOMVideoInstance" in w for w in doc.warnings)
    assert any("x.png" in w for w in doc.warnings)


def test_bitmap_instance_png_in_library(tmp_path):
    from fixtures import frame, layer, NS
    buf = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 255, 0, 255)).save(buf, "PNG")
    inst = '<DOMBitmapInstance libraryItemName="g.png"><matrix><Matrix a="2" d="2" tx="30" ty="20"/></matrix></DOMBitmapInstance>'
    xml = (f'<DOMDocument {NS} width="100" height="100" frameRate="12"><media><DOMBitmapItem name="g.png" href="g.png"/></media>'
           '<timelines><DOMTimeline name="S"><layers>' + layer("a", [frame(0, 1, inst)]) + "</layers></DOMTimeline></timelines></DOMDocument>")
    p = tmp_path / "b.fla"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("DOMDocument.xml", xml)
        z.writestr("LIBRARY/g.png", buf.getvalue())
    doc = xfl.load(p)
    im = render_ops(Evaluator(doc).frame_ops(0), 100, 100, 1.0, None)
    assert px(im, 40, 30)[:3] == (0, 255, 0)        # inside 20x20 box at (30,20)
    assert px(im, 25, 30)[3] == 0 and px(im, 55, 30)[3] == 0


def test_pencil2d_layers_recompose_to_direct_render(doc, tmp_path):
    (out,) = export_pencil2d(doc, tmp_path / "r.pclx", Options())
    z = zipfile.ZipFile(out)
    root = ET.fromstring(z.read("main.xml").decode().split("\n", 1)[1])
    W, H = 200, 100
    ev = Evaluator(doc)
    for g in range(10):
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for layer in root.findall("object/layer"):
            if layer.get("type") != "1":
                continue
            held = [i for i in layer.findall("image") if int(i.get("frame")) <= g + 1]
            if not held:
                continue
            i = held[-1]
            img = Image.open(io.BytesIO(z.read("data/" + i.get("src")))).convert("RGBA")
            canvas.alpha_composite(img, (int(i.get("topLeftX")) + W // 2, int(i.get("topLeftY")) + H // 2))
        ref = render_ops(ev.frame_ops(g), doc.width, doc.height, 1.0, None)
        diff = max(max(abs(a - b) for a, b in zip(p, q)) for p, q in zip(canvas.getdata(), ref.getdata()))
        assert diff <= 3, f"frame {g}: max channel diff {diff}"
