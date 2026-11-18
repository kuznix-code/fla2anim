"""Command line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .export import Options
from .model import FlaError
from . import xfl
from .flatten import Evaluator

TARGETS = {
    "pencil2d": ("pencil2d", "export_pencil2d", ".pclx", "Pencil2D project (bitmap layers)"),
    "synfig": ("synfig", "export_synfig", ".sif", "Synfig Studio vector project"),
    "swf": ("swf", "export_swf", ".swf", "Flash movie (v5 by default); File > Import into Flash/Animate"),
    "opentoonz": ("sequence", "export_opentoonz", "", "PNG sequence name.0001.png for OpenToonz"),
    "harmony": ("sequence", "export_harmony", "", "PNG + SVG sequences for Toon Boom Harmony import"),
    "png": ("sequence", "export_png", "", "PNG image sequence"),
    "svg": ("sequence", "export_svg", "", "SVG image sequence"),
}


def _range(s):
    try:
        a, _, b = s.partition(":")
        return (int(a) - 1 if a else 0, int(b) if b else 10 ** 9)
    except ValueError:
        raise argparse.ArgumentTypeError("use START:END (1-based, inclusive), e.g. 1:24")


def build_parser():
    p = argparse.ArgumentParser(
        prog="fla2anim",
        description="Convert XFL-based Flash projects (CS5+ .fla / .xfl) to other animation formats.")
    p.add_argument("input", nargs="?", help="input .fla / .xfl file or XFL folder")
    p.add_argument("-t", "--to", choices=sorted(TARGETS), help="output format")
    p.add_argument("-o", "--output", help="output file (or folder for sequence formats)")
    p.add_argument("--scale", type=float, default=1.0, help="raster scale factor (default 1.0)")
    p.add_argument("--frames", type=_range, help="frame range START:END, 1-based inclusive")
    p.add_argument("--background", action="store_true", help="include the Flash stage colour as background")
    p.add_argument("--swf-version", type=int, default=5, help="SWF version for -t swf (>=3, default 5)")
    p.add_argument("--supersample", type=int, default=2, help="anti-aliasing factor for raster output (1-4)")
    p.add_argument("--info", action="store_true", help="print project info and exit")
    p.add_argument("--list-targets", action="store_true", help="list output formats and exit")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-V", "--version", action="version", version=f"fla2anim {__version__}")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.list_targets:
        for k, v in sorted(TARGETS.items()):
            print(f"{k:10s} {v[3]}")
        return 0
    if not args.input:
        build_parser().print_usage(sys.stderr)
        print("fla2anim: error: input file required", file=sys.stderr)
        return 2
    if args.scale <= 0 or not 1 <= args.supersample <= 4:
        print("fla2anim: error: --scale must be > 0 and --supersample 1-4", file=sys.stderr)
        return 2
    try:
        doc = xfl.load(args.input)
        if args.info:
            print(f"size {doc.width:g}x{doc.height:g}  fps {doc.fps:g}  frames {doc.total_frames}  "
                  f"scenes {len(doc.timelines)}  symbols {len(doc.symbols)}")
            return 0
        if not args.to:
            print("fla2anim: error: choose an output format with -t/--to (see --list-targets)", file=sys.stderr)
            return 2
        mod, fn, ext, _ = TARGETS[args.to]
        out = args.output or str(Path(args.input).with_suffix("")) + (ext if ext else "_" + args.to)
        opts = Options(scale=args.scale, frames=args.frames, background=args.background,
                       swf_version=args.swf_version, supersample=args.supersample)
        if not args.quiet and sys.stderr.isatty():
            opts.progress = lambda d, t: print(f"\r{d}/{t} frames", end="", file=sys.stderr, flush=True)
        import importlib
        exporter = getattr(importlib.import_module(f"fla2anim.export.{mod}"), fn)
        written = exporter(doc, out, opts, Evaluator(doc))
        if opts.progress:
            print(file=sys.stderr)
    except FlaError as e:
        print(f"fla2anim: error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"fla2anim: error: {e}", file=sys.stderr)
        return 2
    for w in doc.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if not args.quiet:
        if len(written) == 1:
            print(f"wrote {written[0]}")
        else:
            print(f"wrote {len(written)} files under {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
