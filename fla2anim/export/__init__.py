"""Exporters. Each exposes export(doc, out_path, opts) and returns a list of written paths."""
from dataclasses import dataclass


@dataclass
class Options:
    scale: float = 1.0
    frames: tuple = None  # (start, end_exclusive) global frame range, or None
    background: bool = False  # include Flash stage colour as a background
    swf_version: int = 5
    supersample: int = 2
    progress: object = None  # callable(done, total) or None

    def frame_range(self, doc):
        n = doc.total_frames
        if self.frames is None:
            return 0, n
        a, b = self.frames
        a = max(0, min(a, n - 1))
        b = max(a + 1, min(b, n))
        return a, b

    def tick(self, done, total):
        if self.progress:
            self.progress(done, total)
