#!/usr/bin/env python3
"""Layout verification for all README figures.

Assertions:
  1. EDGE-FRAME: the outermost 8px frame is pure background — objective proof
     that no number or label escapes the figure (the overflow check).
  2. PANEL GAP (bench-external): a clean background corridor exists between
     the two panels — proof that nothing crosses panels.
Exit non-zero on any failure. Run after tools/render_charts.sh.
"""
import sys
from PIL import Image

BG = (13, 17, 23)

def frame_clean(path, px=8):
    im = Image.open(path).convert("RGB")
    W, H = im.size
    p = im.load()
    for x in range(W):
        for y in list(range(0, px)) + list(range(H - px, H)):
            if p[x, y] != BG:
                return False, f"ink at top/bottom frame ({x},{y})"
    for y in range(H):
        for x in list(range(0, px)) + list(range(W - px, W)):
            if p[x, y] != BG:
                return False, f"ink at left/right frame ({x},{y})"
    return True, "clean"

def clean_gap(path, y0, y1, min_w=24):
    im = Image.open(path).convert("RGB")
    W, _ = im.size
    p = im.load()
    runs, start = [], None
    for x in range(W):
        clean = all(p[x, y] == BG for y in range(y0, y1, 4))
        if clean and start is None:
            start = x
        if not clean and start is not None:
            if x - start >= min_w:
                runs.append((start, x))
            start = None
    if start is not None and W - start >= min_w:
        runs.append((start, W))
    return runs

checks = []
for f in ("docs/architecture.png", "docs/bench-eir-cost.png",
          "docs/bench-families.png", "docs/bench-external.png"):
    ok, msg = frame_clean(f)
    checks.append((f"{f} edge-frame", ok, msg))

runs = clean_gap("docs/bench-external.png", 300, 420)
ok = any(r[0] < 1100 and r[1] > 900 for r in runs)
checks.append(("bench-external panel gap", ok, str(runs[:4])))

failed = False
for name, ok, msg in checks:
    print(("PASS " if ok else "FAIL "), name, msg if not ok else "")
    failed |= not ok
sys.exit(1 if failed else 0)
