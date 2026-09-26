#!/usr/bin/env python3
"""Generate the README benchmark figures (dark terminal aesthetic).

Charts are hand-rolled SVG (no plotting dependency); numbers mirror
BENCHMARK.md exactly. Rerun after any benchmark update:
    python3 tools/make_bench_charts.py
"""

W1, H1 = 940, 400
W2, H2 = 940, 360
W3, H3 = 940, 340
GREEN, PURPLE, BLUE, GRAY, TXT, DIM = "#3fb950", "#a371f7", "#58a6ff", "#6e7681", "#e6edf3", "#8b949e"
FONT = "'Fira Mono','Fira Code','DejaVu Sans Mono',Menlo,Consolas,monospace"


def header(w, h, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family="{FONT}">'
            f'<rect width="100%" height="100%" fill="#0d1117"/>'
            f'<text x="{w/2}" y="30" font-size="17" fill="{TXT}" text-anchor="middle" '
            f'font-weight="bold">{title}</text>')


def bar_h(x, y, w, h, color, label, value_txt, max_w, dim_label=False):
    c = GRAY if dim_label else color
    return (f'<rect x="{x}" y="{y}" width="{max(w,2)}" height="{h}" rx="4" fill="{c}"/>'
            f'<text x="{x-10}" y="{y+h/2+4}" font-size="12.5" fill="{DIM}" text-anchor="end">{label}</text>'
            f'<text x="{x+max(w,2)+8}" y="{y+h/2+4}" font-size="12.5" fill="{TXT}">{value_txt}</text>')


def chart1():
    s = header(W1, H1, "StepShield holdout (216) — intervention quality vs cost")
    # left panel: EIR3 bars
    rows = [
        ("Edward + Jev 1.13", 0.906, PURPLE),
        ("GPT-4.1-mini judge", 0.89, BLUE),
        ("Edward local 4B", 0.778, GREEN),
        ("HybridGuard (paper)", 0.40, GRAY),
        ("StaticGuard 847 rules", 0.23, GRAY),
    ]
    lx, bw, bh, gap, maxw = 210, 0, 26, 18, 380
    y0 = 70
    s += f'<text x="{lx-10}" y="{y0-14}" font-size="13" fill="{DIM}">EIR₃ (timing)</text>'
    for i, (name, v, color) in enumerate(rows):
        w = v / 1.0 * maxw
        s += bar_h(lx, y0 + i * (bh + gap), w, bh, color, name, f"{v:.3f}", maxw)
    s += (f'<text x="{lx-10}" y="{y0 + 5*(bh+gap) + 12}" font-size="11.5" fill="{DIM}">'
          f'rules-only: recall 7.4% / FPR 1.9% — blind to semantics, included in BENCHMARK.md</text>')
    # right panel: cost vs EIR3 scatter (log-x)
    px0, px1, py0, py1 = 700, 900, 70, 240
    import math
    def X(c):  # log scale 1e-5 .. 1e-3
        return px0 + (math.log10(c) - (-5)) / 2 * (px1 - px0)
    def Y(e):  # 0.70 .. 1.0
        return py1 - (e - 0.70) / 0.30 * (py1 - py0)
    s += f'<text x="{(px0+px1)/2}" y="{py0-14}" font-size="13" fill="{DIM}" text-anchor="middle">cost / decision (log) →</text>'
    for c in (1e-5, 1e-4, 1e-3):
        s += f'<text x="{X(c)}" y="{py1+16}" font-size="10.5" fill="{GRAY}" text-anchor="middle">{c:g}</text>'
    for e in (0.8, 0.9, 1.0):
        s += f'<text x="{px0-8}" y="{Y(e)+4}" font-size="10.5" fill="{GRAY}" text-anchor="end">{e:.1f}</text>'
    pts = [
        ("local 4B", 2e-5, 0.778, GREEN, "($0.00002, EIR₃ 0.78)"),
        ("Jev 1.13", 1e-4, 0.906, PURPLE, "($0.0001, EIR₃ 0.91)"),
        ("GPT-4.1-mini", 5.6e-4, 0.89, BLUE, "(est. $0.00056, EIR₃ 0.89)"),
    ]
    for name, c, e, color, _ in pts:
        cx, cy = X(c), Y(e)
        s += f'<circle cx="{cx}" cy="{cy}" r="7" fill="{color}" fill-opacity="0.9"/>'
        s += f'<text x="{cx}" y="{cy-14}" font-size="11.5" fill="{TXT}" text-anchor="middle">{name}</text>'
    # Pareto arrow: from GPT-4.1-mini to Jev (10x cheaper, higher EIR3)
    s += (f'<path d="M {X(5.6e-4)-12} {Y(0.89)} Q {X(2.5e-4)} {Y(0.94)} {X(1e-4)+12} {Y(0.906)}" '
          f'stroke="{DIM}" stroke-dasharray="4 3" fill="none"/>')
    s += (f'<text x="{(px0+px1)/2}" y="{py1+44}" font-size="11.5" fill="{DIM}" text-anchor="middle">'
          f'Jev: 10× cheaper than the paper judge, higher EIR₃</text>')
    s += (f'<text x="{W1/2}" y="{H1-12}" font-size="11" fill="{GRAY}" text-anchor="middle">'
          f'StepShield (NeurIPS 2026) 216 held-out trajectories · probe v1b · asymmetric confirmation · numbers in BENCHMARK.md</text>')
    return s + "</svg>"


def chart2():
    s = header(W2, H2, "External academic benchmarks — RedCode-Exec + ATBench")
    lx, bh, gap, maxw = 240, 26, 18, 360
    y0 = 66
    s += f'<text x="{lx-10}" y="{y0-14}" font-size="13" fill="{DIM}">RedCode-Exec: unsafe-command recognition</text>'
    rows = [
        ("Edward + Jev probe", 0.705, PURPLE, "70.5%  (0% benign FPR)"),
        ("Edward deterministic", 0.05, GREEN, "5.0%  (0% benign FPR)"),
    ]
    for i, (name, v, color, note) in enumerate(rows):
        s += bar_h(lx, y0 + i * (bh + gap), v * maxw, bh, color, name, note, maxw)
    s += (f'<text x="{lx-10}" y="{y0 + 2*(bh+gap) + 8}" font-size="11.5" fill="{DIM}">'
          f'bash subset n=600 · benign control (n=59) is ours, not the benchmark\'s</text>')
    # right: ATBench F1
    rx, maxw2 = 700, 200
    arows = [
        ("GPT-5.4 (paper)", 0.767, BLUE),
        ("Jev (sweep best)", 0.704, "#c398f9"),
        ("Jev (default)", 0.687, PURPLE),
    ]
    ay0 = 66
    s += f'<text x="{rx-10}" y="{ay0-14}" font-size="13" fill="{DIM}">ATBench: unsafe-trajectory F1</text>'
    for i, (name, v, color) in enumerate(arows):
        s += bar_h(rx, ay0 + i * (bh + gap), v * maxw2, bh, color, name, f"{v:.3f}", maxw2)
    s += (f'<text x="{rx-10}" y="{ay0 + 3*(bh+gap) + 8}" font-size="11.5" fill="{DIM}">'
          f'n=1000 · 38s · ≈$0.1 · trigger-gated mode targets a different axis</text>')
    s += (f'<text x="{W2/2}" y="{H2-12}" font-size="11" fill="{GRAY}" text-anchor="middle">'
          f'Jev 1.13 backend · one batched call per probe battery · raw logs + repro scripts in BENCHMARK.md</text>')
    return s + "</svg>"


def chart3():
    """StepShield recall by attack family: Jev 1.13 vs local 4B."""
    s = header(W3, H3, "StepShield holdout — recall by attack family (per family, n=18 rogue)")
    fams = ["DEC", "INV", "RES", "SEC", "TST", "UFO"]
    names = {"DEC": "deception", "INV": "invest. fraud", "RES": "resource abuse",
             "SEC": "security", "TST": "test fraud", "UFO": "unfair outcome"}
    jev = {"DEC": 6, "INV": 10, "RES": 9, "SEC": 13, "TST": 11, "UFO": 15}
    local = {"DEC": 5, "INV": 11, "RES": 8, "SEC": 13, "TST": 12, "UFO": 14}
    x0, y0, bw, bh, gap = 150, 74, 34, 26, 26
    maxh = 150
    s += f'<text x="{x0-14}" y="{y0-18}" font-size="12.5" fill="{DIM}">recall</text>'
    # gridlines at 1/3, 2/3, 1
    for frac, lab in ((1 / 3, "6/18"), (2 / 3, "12/18"), (1, "18/18")):
        gy = y0 + maxh - frac * maxh
        s += f'<line x1="{x0-6}" y1="{gy}" x2="{x0 + 6*(bw*2+gap)+10}" y2="{gy}" stroke="#21262d"/>'
        s += f'<text x="{x0-14}" y="{gy+4}" font-size="10.5" fill="{GRAY}" text-anchor="end">{lab}</text>'
    for i, f in enumerate(fams):
        x = x0 + i * (bw * 2 + gap)
        base = y0 + maxh
        for j, (vals, color, lab) in enumerate(((jev, PURPLE, "Jev 1.13"), (local, GREEN, "local 4B"))):
            h = vals[f] / 18 * maxh
            s += (f'<rect x="{x + j*bw}" y="{base-h}" width="{bw-3}" height="{h}" rx="3" '
                  f'fill="{color}" fill-opacity="{0.95 if j==0 else 0.8}"/>')
            s += (f'<text x="{x + j*bw + (bw-3)/2}" y="{base-h-5}" font-size="10.5" '
                  f'fill="{TXT}" text-anchor="middle">{vals[f]}</text>')
        s += (f'<text x="{x + bw*2/2 - 2}" y="{base+16}" font-size="12" fill="{DIM}" '
              f'text-anchor="middle">{f}</text>')
        s += (f'<text x="{x + bw*2/2 - 2}" y="{base+31}" font-size="10.5" fill="{GRAY}" '
              f'text-anchor="middle">{names[f]}</text>')
    ly = y0 + maxh + 52
    s += f'<rect x="{x0}" y="{ly-11}" width="12" height="12" rx="3" fill="{PURPLE}"/>'
    s += f'<text x="{x0+18}" y="{ly}" font-size="12" fill="{TXT}">Jev 1.13 (EIR₃ 0.906)</text>'
    s += f'<rect x="{x0+220}" y="{ly-11}" width="12" height="12" rx="3" fill="{GREEN}"/>'
    s += f'<text x="{x0+238}" y="{ly}" font-size="12" fill="{TXT}">local 4B (EIR₃ 0.778)</text>'
    s += (f'<text x="{W3/2}" y="{H3-12}" font-size="11" fill="{GRAY}" text-anchor="middle">'
          f'detection counts from raw logs · both backends share the same recall frontier, differing in false positives</text>')
    return s + "</svg>"


if __name__ == "__main__":
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    (root / "docs" / "bench-eir-cost.svg").write_text(chart1())
    (root / "docs" / "bench-external.svg").write_text(chart2())
    (root / "docs" / "bench-families.svg").write_text(chart3())
    print("written: docs/bench-eir-cost.svg, docs/bench-external.svg, docs/bench-families.svg")
