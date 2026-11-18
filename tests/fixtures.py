"""Build small synthetic XFL projects for tests (no Flash needed)."""
import math
import zipfile

NS = 'xmlns="http://ns.adobe.com/xfl/2008/"'


def tw(v):
    return int(round(v * 20))


def rect_edges(x, y, w, h):
    p = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    out = []
    for i in range(4):
        a, b = p[i], p[(i + 1) % 4]
        out.append(f'<Edge fillStyle1="1" strokeStyle="1" edges="!{tw(a[0])} {tw(a[1])}|{tw(b[0])} {tw(b[1])}"/>')
    return "".join(out)


def circle_edges(cx, cy, r, n=8):
    """Closed circle from n quadratic segments, one Edge element."""
    pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    d = f"!{tw(pts[0][0])} {tw(pts[0][1])}"
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = 2 * math.pi * (i + 1) / n
        am = (a0 + a1) / 2
        rc = r / math.cos((a1 - a0) / 2)
        c = (cx + rc * math.cos(am), cy + rc * math.sin(am))
        e = pts[i + 1]
        d += f"[{tw(c[0])} {tw(c[1])} {tw(e[0])} {tw(e[1])}"
    return f'<Edge fillStyle1="1" edges="{d}"/>'


def shape(fill, edges, stroke=None):
    strokes = ""
    if stroke:
        strokes = (f'<strokes><StrokeStyle index="1"><SolidStroke weight="{stroke[1]}"><fill>'
                   f'<SolidColor color="{stroke[0]}"/></fill></SolidStroke></StrokeStyle></strokes>')
    return (f'<DOMShape><fills><FillStyle index="1"><SolidColor color="{fill}"/></FillStyle></fills>'
            f'{strokes}<edges>{edges}</edges></DOMShape>')


def frame(index, duration, elements, extra=""):
    return f'<DOMFrame index="{index}" duration="{duration}" {extra}><elements>{elements}</elements></DOMFrame>'


def inst(name, tx, ty, stype="graphic", extra=""):
    return (f'<DOMSymbolInstance libraryItemName="{name}" symbolType="{stype}" {extra}>'
            f'<matrix><Matrix tx="{tx}" ty="{ty}"/></matrix></DOMSymbolInstance>')


def layer(name, frames, extra=""):
    return f'<DOMLayer name="{name}" {extra}><frames>{"".join(frames)}</frames></DOMLayer>'


def build_files(frames=10):
    ball = (f'<DOMSymbolItem {NS} name="Ball" symbolType="graphic"><timeline><DOMTimeline name="Ball"><layers>'
            + layer("l", [frame(0, 1, shape("#FF0000", circle_edges(0, 0, 20), ("#000000", 2)))])
            + '</layers></DOMTimeline></timeline></DOMSymbolItem>')
    bg = frame(0, frames, shape("#CCE5FF", rect_edges(0, 0, 200, 100)))
    mover = (frame(0, frames // 2, inst("Ball", 30, 50), 'tweenType="motion"')
             + frame(frames // 2, frames - frames // 2, inst("Ball", 170, 50)))
    # masked layer: green square revealed only through a circle
    masked = frame(0, frames, shape("#00AA00", rect_edges(80, 10, 60, 30)), "")
    maskl = frame(0, frames, shape("#000000", circle_edges(110, 25, 12)), "")
    line = (f'<DOMShape><strokes><StrokeStyle index="1"><SolidStroke weight="3"><fill>'
            f'<SolidColor color="#0000FF"/></fill></SolidStroke></StrokeStyle></strokes><edges>'
            f'<Edge strokeStyle="1" edges="!{tw(10)} {tw(90)}|{tw(190)} {tw(90)}"/></edges></DOMShape>')
    layers = (layer("lines", [frame(0, frames, line)])
              + layer("mover", [mover])
              + layer("masked", [masked], 'parentLayerIndex="3"')
              + layer("mask", [maskl], 'layerType="mask"')
              + layer("bg", [bg]))
    doc = (f'<DOMDocument {NS} width="200" height="100" frameRate="12" backgroundColor="#FFFFFF">'
           '<symbols><Include href="Ball.xml"/></symbols>'
           f'<timelines><DOMTimeline name="Scene 1"><layers>{layers}</layers></DOMTimeline></timelines></DOMDocument>')
    return {"DOMDocument.xml": doc, "LIBRARY/Ball.xml": ball}


def make_fla(path, frames=10):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in build_files(frames).items():
            z.writestr(name, data)
    return path
