# fla2anim

Convert Flash projects to other animation programs, from the command line.

```
fla2anim my_movie.fla -t pencil2d            # -> my_movie.pclx
fla2anim my_movie.fla -t synfig              # -> my_movie.sif
fla2anim my_movie.fla -t swf --swf-version 5 # -> my_movie.swf (Flash 5 compatible)
fla2anim my_movie.fla -t opentoonz           # -> my_movie/  (my_movie.0001.png ...)
fla2anim my_movie.fla -t harmony             # -> my_movie/png + my_movie/svg
```

Requires Python 3.10+ and Pillow (`pip install .`). No other dependencies.

## Input: which `.fla` files work?

| File | Supported |
|---|---|
| Flash CS5 and newer `.fla` (a zip containing `DOMDocument.xml`) | yes |
| Uncompressed `.xfl` (folder with `DOMDocument.xml`) | yes |
| Flash MX 2004 / 8 / CS3 / CS4 `.fla` (legacy binary) | **no** |
| Flash 5 and earlier `.fla` | **no** |

Legacy binary `.fla` files are a proprietary, undocumented format. fla2anim detects them and stops with
a message instead of guessing. To convert one, open it in Adobe Animate / Flash CS5+ and use
*File > Save As > Flash CS5+ Uncompressed Document (.xfl)*, then run fla2anim on that.

## Output formats

| `-t` | Result | Notes |
|---|---|---|
| `pencil2d` | `.pclx` | One bitmap layer per Flash layer, one keyframe per change. Rasterised (Pencil2D vector layers are not written). |
| `synfig` | `.sif` | Real vector layers (regions + outlines), one group per unchanged run of a layer, visibility animated with constant waypoints. Curves are flattened to polylines. |
| `swf` | `.swf` | SWF version 3+ (default **5**, i.e. Macromedia Flash 5 compatible). Import it into Flash/Animate (*File > Import*) to get an editable timeline. Symbols are flattened to plain shapes. |
| `opentoonz` | folder of `name.0001.png` | Load with *File > Load Level*; OpenToonz treats it as one level. |
| `harmony` | `png/` and `svg/` sequences | Import with *File > Import > Images* in Toon Boom Harmony. |
| `png`, `svg` | image sequences | Generic. |

There is no direct writer for OpenToonz `.tnz`, Harmony `.xstage`/`.tvg` or Flash `.fla`: those formats are
either undocumented or too fragile to generate blind. Image sequences and SWF are the reliable bridges.
In particular, **writing a legacy Flash 5 `.fla` is not possible**; `-t swf --swf-version 5` is the closest
supported route (SWF 5 plays in, and can be imported into, Flash 5).

Options: `--scale N`, `--frames START:END` (1-based, inclusive), `--background` (include the stage colour),
`--supersample 1-4`, `--swf-version N`, `--info`, `--list-targets`.

## What is converted

Shapes (fills, strokes, quadratic curves, holes, shared edges), rectangle/oval primitives, groups,
nested graphic / movie clip / button symbols (loop, play once, single frame), classic motion tweens
(translation, scale, rotation, easing, alpha), mask layers (Pencil2D, PNG, SVG), alpha/tint/brightness
effects, PNG/JPEG library bitmaps, multiple scenes (concatenated).

## Known limitations (0.1.0)

* **Gradient fills** are approximated by the average of their colours; **bitmap fills** become grey.
* **Shape tweens** and CS4+ **Motion Tween** objects are not interpolated: the first keyframe is held.
* **Text** is rendered with a built-in font (position/size approximate); Synfig and SWF output omit text.
* **Bitmaps stored only as Flash-internal `bin/*.dat` data** cannot be decoded and are skipped (with a warning).
  Bitmaps that are plain PNG/JPEG in the project are used.
* **Masks** are applied in raster/SVG output but not in Synfig/SWF output (a warning is printed).
* Movie clips play from the keyframe they appear on; ActionScript, sound, video, filters and blend modes are ignored.
* Dashed/ragged stroke styles are drawn solid.
* Guide layers, folders and hidden layers are not exported.
* The Pencil2D, Synfig and SWF files are produced from the documented/known structure of those formats and are
  checked structurally by the test suite (SWF is decoded back by an independent reader in the tests), but the
  target applications themselves are not part of the test run. Please report any file that does not open.

## Development

```
pip install pillow pytest
python -m pytest
```

Tests build synthetic XFL projects on the fly (`tests/fixtures.py`); no Flash installation is needed.

## License

MIT
