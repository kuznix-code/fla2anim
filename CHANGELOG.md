# Changelog

## 0.1.1
Bug-fix release.

- Fix crash (raw `BadZipFile` traceback) on CS5+ `.fla` files whose zip central directory is damaged or
  truncated. Entries are now recovered from the local file headers, with a warning.
- Detect the input format by magic bytes before choosing a reader.
- Clearer error for legacy binary `.fla` (Flash 5 - CS4, including Flash 8): still unsupported, now says so.
- Unexpected errors now print a one-line message instead of a traceback; add `--debug` for the full traceback.

## 0.1.0
First release.

- Reads CS5+ `.fla` (zip/XFL) and uncompressed `.xfl` projects.
- Evaluates timelines: shapes (fills, strokes, curves, holes, shared edges), rectangle/oval primitives,
  groups, nested graphic/movie-clip/button symbols, classic motion tweens (with easing and rotation),
  mask layers, alpha/tint/brightness colour effects, PNG/JPEG library bitmaps, basic static text, multiple scenes.
- Exporters: `pencil2d` (.pclx), `synfig` (.sif), `swf` (version 3+, default 5), `opentoonz` (PNG sequence),
  `harmony` (PNG + SVG sequences), `png`, `svg`.
- Legacy binary `.fla` (Flash 5 - CS4) is detected and rejected with a clear message.
