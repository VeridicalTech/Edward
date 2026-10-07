#!/usr/bin/env python3
"""Replay REAL agent session logs through Edward's control plane.

Sources (auto-discovered):
  - Codex CLI:  ~/.codex/sessions/**/*.jsonl  (exec_command / apply_patch)
  - Claude Code: ~/.claude/projects/**/*.jsonl (Bash/Write/Edit tool_use)

Every tool step is passed through (a) the deterministic rules layer and
(b) the semantic scorer (contract probe v1b, --style ollama direct readout
at 127.0.0.1:8001). Ground truth: none — these are benign work sessions, so
every detection is a candidate false positive for the real-work FPR number,
and every receipt is a demo of Edward on a real event stream.
"""
import glob
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/Edward/repo")
import edward.stepshield as ss
from edward.stepshield import StepTrajectory, evaluate_mode

MAX_SCORED_STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
OUT = Path("/root/Edward/repo/results/raw/replay_real_sessions.log")


def iter_codex_steps(path):
    """exec_command -> run_command; apply_patch -> edit_file."""
    task = ""
    step = 0
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        if j.get("type") != "response_item":
            if j.get("type") == "event_msg" and not task:
                p = j.get("payload", {})
                m = p.get("message")
                if p.get("type") == "user_message" and isinstance(m, str):
                    task = m[:160]
            continue
        p = j.get("payload", {})
        if p.get("type") != "function_call":
            continue
        name = p.get("name", "")
        try:
            args = json.loads(p.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        step += 1
        if name == "exec_command":
            cmd = args.get("cmd") or args.get("command") or ""
            if isinstance(cmd, list):
                cmd = " ".join(str(c) for c in cmd)
            yield {"step": step, "action": "run_command",
                   "arguments": {"command": str(cmd)[:2000]},
                   "observation": "", "thought": ""}
        elif name == "apply_patch":
            yield {"step": step, "action": "edit_file",
                   "arguments": {"file_path": "(patch)"},
                   "observation": str(args.get("input") or args.get("patch") or "")[:400],
                   "thought": ""}


def iter_claude_steps(path):
    task = ""
    step = 0
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        if j.get("type") == "user" and not task:
            c = j.get("message", {}).get("content")
            if isinstance(c, str):
                task = c[:160]
        if j.get("type") != "assistant":
            continue
        for b in j.get("message", {}).get("content", []):
            if b.get("type") != "tool_use":
                continue
            step += 1
            name, inp = b.get("name", ""), b.get("input", {}) or {}
            if name == "Bash":
                yield {"step": step, "action": "run_command",
                       "arguments": {"command": str(inp.get("command", ""))[:2000]},
                       "observation": "", "thought": ""}
            elif name in ("Write", "Edit", "MultiEdit"):
                act = {"Write": "write_file", "Edit": "edit_file", "MultiEdit": "edit_file"}[name]
                yield {"step": step, "action": act,
                       "arguments": {"file_path": str(inp.get("file_path", ""))[:400]},
                       "observation": "", "thought": ""}


def collect():
    trajs = []
    for path in sorted(glob.glob("/root/.codex/sessions/**/*.jsonl", recursive=True)):
        meta_id = Path(path).stem[-8:]
        trajs.append((f"codex-{meta_id}", path, iter_codex_steps))
    for path in sorted(glob.glob("/root/.claude/projects/*/*.jsonl")):
        meta_id = Path(path).stem[-8:]
        trajs.append((f"claude-{meta_id}", path, iter_claude_steps))
    out = []
    for tid, path, it in trajs:
        steps = list(it(path))
        if not steps:
            continue
        if len(steps) > MAX_SCORED_STEPS:
            steps = steps[:MAX_SCORED_STEPS]
        out.append(StepTrajectory(
            trajectory_id=tid,
            task_title=steps[0].get("thought") or tid,
            description=f"real session {tid}",
            steps=steps, trajectory_type="clean", category="real"))
    return out


def main():
    trajs = collect()
    n_events = sum(len(t.steps) for t in trajs)
    print(f"real sessions: {len(trajs)}, tool steps (capped {MAX_SCORED_STEPS}/session): {n_events}",
          flush=True)
    OUT.write_text("")
    def log(msg):
        print(msg, flush=True)
        with OUT.open("a") as f:
            f.write(msg + "\n")
    t0 = time.time()
    suite = ss.evaluate_mode(trajs, "balanced", "contract",
                             scorer_base_url="http://127.0.0.1:8001",
                             log=log, confirm_mode="asymmetric", probe="v1b")
    m = suite.compute()
    log(f"RESULT real-sessions: flagged {sum(r.detected for r in suite.results)}/{len(trajs)} "
        f"sessions, {sum(r.detected for r in suite.results)}/{n_events} steps "
        f"(flag-rate {m['fpr_clean']:.1%}) ({time.time()-t0:.0f}s)")
    print("DONE")


if __name__ == "__main__":
    main()
