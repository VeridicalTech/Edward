#!/usr/bin/env python3
"""Render a real Edward transcript into a terminal-style SVG for the README.

Usage: python3 tools/make_demo_svg.py docs/demo.svg
The transcript below is captured verbatim from an actual `edward wrap` run
(StepShield-free live pi session, passive-stall intervention).
"""
import sys

TRANSCRIPT = [
    ("dim",   "$ edward wrap --no-scorer -- pi 'Read g01.txt ... g14.txt one at a time'"),
    ("dim",   "[09:44:16] scorer ready: Qwen/Qwen3.5-4B"),
    ("dim",   "[09:44:17] task: Read the following files one at a time ..."),
    ("txt",   "[09:44:21] tool: read [OK]"),
    ("txt",   "[09:44:23] tool: read [OK]"),
    ("txt",   "[09:44:25] tool: read [OK]"),
    ("dim",   "     ... 8 more reads ..."),
    ("txt",   "[09:44:49] tool: read [OK]"),
    ("warn",  "[09:44:50] TRIGGER: Passive stall: 12 consecutive reads, zero file modifications"),
    ("warn",  "[09:44:50] MSS: {turn_count: 12, token_usage: 3589, files_modified_count: 0, ...}"),
    ("good",  "[09:44:50] JEV RESULT: CANCEL (conf 0.58)"),
    ("bad",   "============================================================"),
    ("bad",   "[EDWARD] CANCEL: Passive stall: 12 consecutive reads, zero file modifications"),
    ("bad",   "============================================================"),
    ("dim",   "[09:44:51] stopped at 3589 tokens (~$0.00 spent this session)"),
    ("dim",   "$ edward audit"),
    ("good",  "sessions: 2   interventions: 1   by action: {'CANCEL': 1}"),
]

COLORS = {"dim": "#7a7f8a", "txt": "#c9d1d9", "warn": "#e3b341",
          "good": "#57d364", "bad": "#f85149"}


def render(path: str) -> None:
    lines = TRANSCRIPT
    lh = 22
    pad = 24
    width = 900
    height = pad * 2 + 44 + lh * len(lines)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Menlo,Consolas,monospace">',
        f'<rect width="100%" height="100%" rx="10" fill="#0d1117"/>',
        f'<rect width="100%" height="36" rx="10" fill="#161b22"/>',
        '<circle cx="20" cy="18" r="6" fill="#f85149"/>'
        '<circle cx="40" cy="18" r="6" fill="#e3b341"/>'
        '<circle cx="60" cy="18" r="6" fill="#57d364"/>',
        f'<text x="450" y="23" text-anchor="middle" fill="#8b949e" font-size="13">'
        f'edward — live intervention</text>',
    ]
    y = 44 + pad - 6
    for kind, text in lines:
        color = COLORS[kind]
        weight = '600' if kind in ("warn", "bad", "good") else '400'
        escaped = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        parts.append(f'<text x="{pad}" y="{y}" fill="{color}" font-size="13.5" '
                     f'font-weight="{weight}" xml:space="preserve">{escaped}</text>')
        y += lh
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    print(f"wrote {path}")


if __name__ == "__main__":
    render(sys.argv[1] if len(sys.argv) > 1 else "docs/demo.svg")
