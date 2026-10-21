"""edward mcp: stdio JSON-RPC loop — initialize, tools/list, and the
report_event supervision primitive (dangerous bash -> TERMINATE decision)."""
import io
import json
import unittest

from edward.mcp_server import handle_message, McpSession, serve


def rpc(method, mid=None, **params):
    m = {"jsonrpc": "2.0", "method": method}
    if mid is not None:
        m["id"] = mid
    if params:
        m["params"] = params
    return json.dumps(m)


class TestMcpProtocol(unittest.TestCase):
    def setUp(self):
        self.session = McpSession(policy_path="balanced", audit_path=None)

    def test_initialize(self):
        resp = handle_message(json.loads(rpc("initialize", mid=1)), self.session)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "edward")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list(self):
        resp = handle_message(json.loads(rpc("tools/list", mid=2)), self.session)
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertIn("report_event", names)
        self.assertIn("judge_action", names)

    def test_report_event_benign_then_dangerous(self):
        benign = handle_message(json.loads(rpc(
            "tools/call", mid=3, name="report_event",
            arguments={"tool": "read", "args": {"path": "src/a.py"}})), self.session)
        out = json.loads(benign["result"]["content"][0]["text"])
        self.assertEqual(out["decision"]["action"], "CONTINUE")

        danger = handle_message(json.loads(rpc(
            "tools/call", mid=4, name="report_event",
            arguments={"tool": "bash", "args": {"command": "rm -rf /"}})), self.session)
        out = json.loads(danger["result"]["content"][0]["text"])
        self.assertNotEqual(out["decision"]["action"], "CONTINUE")
        self.assertIn(out["decision"]["action"],
                      ("TERMINATE", "CANCEL", "BLOCK", "PAUSE", "REQUEST_HUMAN_APPROVAL"))

    def test_unknown_tool_is_error(self):
        resp = handle_message(json.loads(rpc(
            "tools/call", mid=5, name="nope", arguments={})), self.session)
        self.assertTrue(resp["result"].get("isError"))

    def test_unknown_method(self):
        resp = handle_message(json.loads(rpc("wat/never", mid=6)), self.session)
        self.assertEqual(resp["error"]["code"], -32601)


class TestMcpStdio(unittest.TestCase):
    def test_serve_scripted_session(self):
        lines = "\n".join([
            rpc("initialize", mid=1),
            rpc("notifications/initialized"),
            rpc("tools/call", mid=2, name="report_event",
                arguments={"tool": "bash", "args": {"command": "git status"}}),
            rpc("ping", mid=3),
        ]) + "\n"
        out = io.StringIO()
        serve(io.StringIO(lines), out, policy_path="balanced", audit_path=None)
        rows = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(rows), 3)                      # notifications get no reply
        self.assertEqual(rows[0]["id"], 1)
        call = json.loads(rows[1]["result"]["content"][0]["text"])
        self.assertEqual(call["decision"]["action"], "CONTINUE")
        self.assertEqual(rows[2]["id"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
