#!/usr/bin/env python3
"""Build asciinema .cast files from real Edward run transcripts, then the
caller renders GIFs with agg. Timestamps drive inter-line pacing (compressed).
Usage: python3 tools/make_cast.py <out.cast> <speed>
Then: agg --speed <speed> --cols 100 --rows 32 <out.cast> <out.gif>
Lines come from genuine `edward wrap` logs (see transcript blocks below).
"""
import json
import sys
import time

KIND_COLOR = {"dim": "#7a7f8a", "txt": "#c9d1d9", "warn": "#e3b341",
              "good": "#57d364", "bad": "#f85149"}

# (kind, text, seconds_since_prev) — captured verbatim from real runs
SCENARIOS = {
    "intervention": [
        ("cmd", "edward wrap --no-scorer -- pi 'Read g01.txt ... g14.txt one at a time'", 0.0),
        ("dim", "[09:44:16] scorer ready: Qwen/Qwen3.5-4B", 0.6),
        ("dim", "[09:44:17] task: Read the following files one at a time ...", 0.5),
        ("txt", "[09:44:21] tool: read [OK]", 2.0),
        ("txt", "[09:44:23] tool: read [OK]", 1.8),
        ("txt", "[09:44:25] tool: read [OK]", 1.7),
        ("dim", "     ... 8 more reads, zero writes ...", 4.0),
        ("txt", "[09:44:49] tool: read [OK]", 2.2),
        ("warn", "[09:44:50] TRIGGER: Passive stall: 12 consecutive reads, zero file modifications", 0.9),
        ("warn", "[09:44:50] MSS: {turn_count: 12, token_usage: 3589, files_modified_count: 0, ...}", 0.4),
        ("good", "[09:44:50] JEV RESULT: CANCEL (conf 0.59)", 0.5),
        ("bad",  "[EDWARD] CANCEL: Passive stall: 12 consecutive reads, zero file modifications", 0.5),
        ("dim",  "[09:44:51] stopped at 3589 tokens (~$0.00 spent this session)", 0.7),
        ("dim",  "exit code 76", 0.5),
    ],
    "resume_verify": [
        ("cmd", "edward wrap --continue --no-scorer -- pi 'create resume_done.txt ...'", 0.0),
        ("dim", "resuming session edward-39a1f1d5", 0.7),
        ("txt", "[11:24:24] tool: write [OK]", 8.0),
        ("txt", "[11:24:26] tool: read [OK]", 2.0),
        ("good", "[11:24:30] agent settled", 3.5),
        ("good", 'summary: {"turn_count": 3, "total_tool_calls": 2, "files_modified": 1, "interventions": 0}', 0.5),
        ("cmd", "edward verify", 1.5),
        ("good", "audit:    /root/.edward/audit.jsonl (2 records)", 0.6),
        ("good", "verdict:  VALID", 0.6),
    ],
    "demo": [
        ("cmd", "edward demo", 0.0),
        ("dim", "policy: balanced  trials/scenario: 5", 1.2),
        ("txt", "normal            no-fire    clean               —  ok", 0.8),
        ("txt", "transient_failure no-fire    clean               —  ok", 0.4),
        ("txt", "infinite_loop     fire       100% detected      8.0  ok", 0.5),
        ("txt", "budget_bleed      fire       100% detected     12.0  ok", 0.4),
        ("txt", "dangerous         fire       100% detected       5.2  ok", 0.4),
        ("txt", "stall             fire       100% detected       4.0  ok", 0.4),
        ("good", "PASS in 0.0s (deterministic rules frozen defaults; scorer off)", 1.2),
    ],
}


def build(out_cast: str, scenario: str, speed: float = 4.0):
    lines = SCENARIOS[scenario]
    header = {"version": 2, "width": 100, "height": 30, "timestamp": int(time.time()),
              "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"}}
    with open(out_cast, "w") as fh:
        fh.write(json.dumps(header) + "\n")
        t = 0.0
        for kind, text, delay in lines:
            t += delay / speed
            color = KIND_COLOR.get(kind, "#c9d1d9")
            # ANSI 256-color approximations: emit escape codes around text
            palette = {"cmd": "38;5;39", "dim": "38;5;245", "txt": "38;5;250",
                       "warn": "38;5;179", "good": "38;5;114", "bad": "38;5;203"}
            payload = f"\x1b[{palette.get(kind, '0')}m{text}\x1b[0m\r\n"
            fh.write(json.dumps([t, "o", payload]) + "\n")
        fh.write(json.dumps([t + 1.2 / speed, "m", "edward@demo"]) + "\n")
    print(f"wrote {out_cast} ({scenario})")


if __name__ == "__main__":
    out, scenario = sys.argv[1], sys.argv[2]
    speed = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    build(out, scenario, speed)
