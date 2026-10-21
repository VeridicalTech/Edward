"""edward mcp — expose the control plane to the agent over MCP (stdio).

Zero-dependency MCP server: JSON-RPC 2.0, one message per stdin line.
The in-loop supervision primitive is `report_event`: the agent (or its
harness) reports each tool execution; Edward answers with the deterministic
decision (CONTINUE / PAUSE / CANCEL / TERMINATE) exactly as `edward wrap`
would decide, plus — when a scorer is configured — a semantic judgment.

Tools:
  report_event   {tool, args, is_error?}  -> decision + state snapshot
  judge_action   {question, options?}     -> scorer verdict (advisory)
  get_state      {}                       -> MSS snapshot
  session_summary{}                       -> interventions so far

Run: edward mcp [--policy balanced]   (stdio; logs to stderr only)
"""
import json
import sys
import uuid

from .audit import AuditLog
from .engine import ControlPlane
from .scorer import make_scorer

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "edward", "version": "0.4.2"}

TOOLS = [
    {"name": "report_event", "description": (
        "Report one tool execution to the Edward control plane. Returns the "
        "deterministic decision (CONTINUE/PAUSE/CANCEL/TERMINATE/WAIT). On "
        "PAUSE or worse, stop and surface decision.reason to the operator."),
     "inputSchema": {"type": "object", "properties": {
         "tool": {"type": "string", "description": "bash|write|edit|read|grep|browse|..."},
         "args": {"type": "object", "description": "e.g. {\"command\": \"...\"} or {\"path\": \"...\"}"},
         "is_error": {"type": "boolean"}},
         "required": ["tool", "args"]}},
    {"name": "judge_action", "description": (
        "Advisory semantic judgment on a planned action (scorer optional; "
        "falls back to rule-free heuristic when offline)."),
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string"},
         "options": {"type": "object", "description": "option id -> description"}},
         "required": ["question"]}},
    {"name": "get_state", "description": "Current supervised-state snapshot (MSS).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "session_summary", "description": "Interventions and counters for this MCP session.",
     "inputSchema": {"type": "object", "properties": {}}},
]


class McpSession:
    def __init__(self, policy_path=None, audit_path=None):
        from .config import load_policy
        self.policy = load_policy(policy_path or "balanced")
        self.session = uuid.uuid4().hex[:8]
        self.audit = AuditLog(audit_path)
        self.audit.session_start(session=self.session,
                                 policy_preset=self.policy.preset,
                                 command=["edward", "mcp"])
        scorer = None
        if self.policy.scorer_enabled:
            scorer = make_scorer(self.policy)
            if not (scorer.health() or {}).get("ready"):
                scorer = None
        self.plane = ControlPlane(self.policy, session=self.session,
                                  audit=self.audit, scorer=scorer,
                                  log=lambda m: print(f"[edward] {m}", file=sys.stderr))
        self.interventions = []

    # -- tool impls ---------------------------------------------------------
    def report_event(self, params):
        event = {"type": "tool_execution_end",
                 "toolName": str(params.get("tool", "")),
                 "args": params.get("args") or {},
                 "isError": bool(params.get("is_error", False))}
        decision = self.plane.process_event(event)
        result = {"state": self.plane.state.to_mss(),
                  "decision": ({"action": decision.action,
                                "reason": decision.reason,
                                "source": decision.source}
                               if decision else
                               {"action": "CONTINUE",
                                "reason": "no trigger fired",
                                "source": "rule (no trigger)"})}
        if decision and decision.action not in ("CONTINUE", "WAIT"):
            self.interventions.append(result["decision"])
        return result

    def judge_action(self, params):
        question = str(params.get("question", ""))
        options = params.get("options") or {"OK": "benign",
                                            "VIOLATION": "violates a constraint",
                                            "UNSURE": "ambiguous"}
        scorer = self.plane.scorer
        if scorer is None:
            return {"verdict": "UNSCORED",
                    "note": "scorer unreachable — rule-only mode; deterministic "
                            "checks still apply via report_event"}
        answer = scorer.backend.score({"session": self.session}, question, options)
        return {"choice": (answer or {}).get("choice"),
                "confidence": (answer or {}).get("confidence"),
                "probabilities": (answer or {}).get("probabilities"),
                "note": "advisory only; deterministic layer owns irreversible actions"}

    def get_state(self, _params):
        return {"state": self.plane.state.to_mss()}

    def session_summary(self, _params):
        return {"session": self.session, "interventions": self.interventions,
                "tool_calls": self.plane.state.total_tool_calls,
                "tokens": self.plane.state.token_usage}

    # -- dispatch -----------------------------------------------------------
    def call_tool(self, name, params):
        if name == "report_event":
            return self.report_event(params)
        if name == "judge_action":
            return self.judge_action(params)
        if name == "get_state":
            return self.get_state(params)
        if name == "session_summary":
            return self.session_summary(params)
        raise ValueError(f"unknown tool: {name}")


def handle_message(msg, session: McpSession):
    method = msg.get("method", "")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"protocolVersion": PROTOCOL_VERSION,
                           "capabilities": {"tools": {}},
                           "serverInfo": SERVER_INFO}}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            result = session.call_tool(params.get("name", ""),
                                       params.get("arguments") or {})
            text = json.dumps(result, default=str, ensure_ascii=False)
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": f"error: {exc}"}],
                               "isError": True}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def serve(stdin=None, stdout=None, policy_path=None, audit_path=None):
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    session = McpSession(policy_path=policy_path, audit_path=audit_path)
    print(f"edward mcp: session edward-{session.session} "
          f"(scorer {'on' if session.plane.scorer else 'off'})", file=sys.stderr)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = handle_message(msg, session)
        except Exception as exc:
            mid = msg.get("id") if isinstance(msg, dict) else None
            resp = {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": str(exc)[:300]}}
        if resp is not None:
            stdout.write(json.dumps(resp, default=str, ensure_ascii=False) + "\n")
            stdout.flush()
    session.audit.session_end(session=session.session, reason="stdin closed",
                              exit_code=0)
