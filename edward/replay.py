"""edward replay — render one audited session as a self-contained HTML
receipt view. Pure stdlib; the output file has no external dependencies
(inline CSS, no JS frameworks) and can be shared as-is.

The point: receipts should be *lookable at*, not just greppable. A session
becomes a page — command, timeline, every intervention with its trigger,
MSS snapshot, scorer verdict and estimated avoided spend — signed trail
noted at the bottom (`edward verify`).
"""
import html
import json
from pathlib import Path

_DARK_CSS = """
body{background:#0d1117;color:#c9d1d9;font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;margin:0;padding:2rem;max-width:60rem;margin-inline:auto}
h1{font-size:1.15rem;color:#e6edf3}h2{font-size:.95rem;color:#8b949e;text-transform:uppercase;letter-spacing:.08em;margin-top:2rem}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:1rem 1.25rem;margin:.75rem 0}
.card.int{border-left:3px solid #d29922}
.card.end{border-left:3px solid #3fb950}
.card.start{border-left:3px solid #58a6ff}
.k{color:#8b949e}.v{color:#e6edf3}.ts{color:#7ee787;font-size:.85rem}
.badge{display:inline-block;background:#21262d;border:1px solid #30363d;border-radius:12px;padding:.05rem .6rem;margin-right:.4rem;font-size:.8rem}
.badge.PAUSE{color:#d29922}.badge.CANCEL,.badge.BLOCK,.badge.TERMINATE{color:#f85149}
.badge.CONTINUE,.badge.WAIT{color:#3fb950}
pre{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:.6rem;overflow-x:auto;font-size:.8rem}
a{color:#58a6ff}.foot{color:#8b949e;font-size:.8rem;margin-top:2.5rem;border-top:1px solid #21262d;padding-top:1rem}
table{border-collapse:collapse;font-size:.85rem}td,th{border:1px solid #30363d;padding:.2rem .6rem;text-align:left}
"""


def _esc(v):
    return html.escape(json.dumps(v, default=str, ensure_ascii=False, indent=1)
                       if not isinstance(v, str) else v)


def load_records(audit_path):
    recs = []
    with open(audit_path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                recs.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return recs


def sessions_in(recs):
    seen = []
    for r in recs:
        sid = r.get("session")
        if sid and sid not in seen:
            seen.append(sid)
    return seen


def render(audit_path, session=None, receipts_path=None):
    recs = load_records(audit_path)
    sids = sessions_in(recs)
    if not sids:
        raise ValueError("no sessions found in audit log")
    sid = session or sids[-1]
    rows = [r for r in recs if r.get("session") == sid]
    if not rows:
        raise ValueError(f"session {sid!r} not found (available: {', '.join(sids[-5:])})")

    out = [f"<!doctype html><html><head><meta charset='utf-8'>",
           f"<title>edward replay — {html.escape(sid)}</title>",
           f"<style>{_DARK_CSS}</style></head><body>",
           f"<h1>edward replay — session <span class='ts'>{html.escape(sid)}</span></h1>"]

    for r in rows:
        t = r.get("type", "?")
        ts = r.get("ts", "")
        if t == "session_start":
            cmd = " ".join(r.get("command") or [])
            out.append(f"<div class='card start'><span class='ts'>{html.escape(ts)}</span> "
                       f"<span class='badge'>SESSION START</span><br>"
                       f"<span class='k'>command:</span> <span class='v'>{html.escape(cmd)}</span><br>"
                       f"<span class='k'>policy:</span> <span class='v'>{html.escape(str(r.get('policy_preset', '')))}</span></div>")
        elif t == "intervention":
            jev = r.get("jev") or {}
            jev_bit = ""
            if jev:
                jev_bit = (f"<span class='k'>scorer:</span> <span class='v'>"
                           f"{html.escape(str(jev.get('choice', jev.get('action', '?'))))}</span> ")
            mss = r.get("mss") or {}
            est = r.get("est_avoided_usd")
            est_bit = (f"<br><span class='k'>est. avoided spend:</span> "
                       f"<span class='v'>${est:.2f}</span>") if est else ""
            out.append(f"<div class='card int'><span class='ts'>{html.escape(ts)}</span> "
                       f"<span class='badge {html.escape(str(r.get('action', '')))}'>{html.escape(str(r.get('action', '?')))}</span>"
                       f"{jev_bit}<br>"
                       f"<span class='k'>trigger:</span> <span class='v'>{html.escape(str(r.get('trigger_reason', '')))}</span><br>"
                       f"<span class='k'>source:</span> <span class='v'>{html.escape(str(r.get('source', '')))}</span> "
                       f"<span class='k'>authority:</span> <span class='v'>{html.escape(str(r.get('authority', '')))}</span>"
                       f"{est_bit}"
                       f"<details><summary class='k'>MSS snapshot</summary><pre>{_esc(mss)}</pre></details></div>")
        elif t == "session_end":
            out.append(f"<div class='card end'><span class='ts'>{html.escape(ts)}</span> "
                       f"<span class='badge'>SESSION END</span> "
                       f"<span class='k'>exit:</span> <span class='v'>{html.escape(str(r.get('exit_code', '')))}</span> "
                       f"<span class='k'>{html.escape(str(r.get('reason', '')))}</span></div>")
        elif t == "scorer_stats":
            out.append(f"<div class='card'><span class='ts'>{html.escape(ts)}</span> "
                       f"<span class='k'>scorer stats:</span> <span class='v'>{html.escape(str(r.get('stats', r)))[:300]}</span></div>")
        elif t == "done_verified":
            out.append(f"<div class='card'><span class='ts'>{html.escape(ts)}</span> "
                       f"<span class='badge CONTINUE'>DONE VERIFIED</span> "
                       f"<span class='v'>{html.escape(str(r.get('detail', '')))[:200]}</span></div>")

    foot = (f"<div class='foot'>generated by <span class='v'>edward replay</span> from "
            f"<span class='v'>{html.escape(str(audit_path))}</span> · "
            f"{len(rows)} records · signed trail: <span class='v'>edward verify</span>"
            + (f" · receipts: <span class='v'>{html.escape(str(receipts_path))}</span>"
               if receipts_path else "")
            + "<br>next: <span class='v'>edward wrap -- &lt;your agent&gt;</span> to "
            "supervise a run, then <span class='v'>edward replay</span> again — "
            "this page is a single file, safe to attach to a review or an issue."
            "</div></body></html>")
    out.append(foot)
    return "\n".join(out), sid


def write_replay(audit_path, out_path=None, session=None):
    audit_path = Path(audit_path)
    html_text, sid = render(audit_path, session=session)
    out = Path(out_path) if out_path else audit_path.parent / f"replay-{sid}.html"
    out.write_text(html_text, encoding="utf-8")
    return out, sid
