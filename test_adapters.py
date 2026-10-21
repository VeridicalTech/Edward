"""Adapter tests: claude-code / codex stream translation to canonical events.
Fixtures are real lines captured from live CLI output (claude 2.1.167,
codex exec --json) and on-disk session rollouts."""
import unittest

from edward.adapters import ClaudeAdapter, CodexAdapter, resolve_adapter


class TestClaudeAdapter(unittest.TestCase):
    def test_build_command_adds_stream_flags(self):
        out = ClaudeAdapter.build_command(["claude", "-p", "fix the test"])
        self.assertIn("--output-format", out)
        self.assertIn("stream-json", out)
        self.assertIn("--verbose", out)

    def test_build_command_inserts_p_when_missing(self):
        out = ClaudeAdapter.build_command(["claude", "fix the test"])
        self.assertIn("-p", out)
        self.assertEqual(out[out.index("-p") + 1], "fix the test")

    def test_extract_prompt(self):
        self.assertEqual(ClaudeAdapter.extract_prompt(
            ["claude", "-p", "fix it", "--model", "x"]), "fix it")

    def test_parse_system_init(self):
        line = ('{"type":"system","subtype":"init","session_id":"88b",'
                '"cwd":"/w","tools":["Bash"]}')
        self.assertEqual(ClaudeAdapter.parse_line(line), [{"type": "agent_start"}])

    def test_parse_tool_use(self):
        line = ('{"type":"assistant","message":{"content":['
                '{"type":"text","text":"running"},{"type":"tool_use",'
                '"id":"t1","name":"Bash","input":{"command":"ls -la"}},'
                '{"type":"tool_use","id":"t2","name":"Edit",'
                '"input":{"file_path":"a.py"}}]}}')
        events = ClaudeAdapter.parse_line(line)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["toolName"], "bash")
        self.assertEqual(events[0]["args"]["command"], "ls -la")
        self.assertEqual(events[1]["toolName"], "edit")
        self.assertEqual(events[1]["args"]["path"], "a.py")

    def test_parse_retry_and_result(self):
        retry = ('{"type":"system","subtype":"api_retry","attempt":1}')
        self.assertEqual(ClaudeAdapter.parse_line(retry),
                         [{"type": "auto_retry_start"}])
        self.assertEqual(ClaudeAdapter.parse_line('{"type":"result"}'),
                         [{"type": "agent_end"}])

    def test_parse_plain_text(self):
        self.assertEqual(ClaudeAdapter.parse_line("hello world"), [])


class TestCodexAdapter(unittest.TestCase):
    def test_build_command_inserts_exec_and_json(self):
        self.assertEqual(
            CodexAdapter.build_command(["codex", "do it"]),
            ["codex", "exec", "--json", "do it"])
        self.assertEqual(
            CodexAdapter.build_command(["codex", "exec", "--json", "x"]),
            ["codex", "exec", "--json", "x"])

    def test_extract_prompt(self):
        self.assertEqual(CodexAdapter.extract_prompt(
            ["codex", "exec", "--json", "do it"]), "do it")

    def test_parse_live_items(self):
        self.assertEqual(CodexAdapter.parse_line(
            '{"type":"thread.started","thread_id":"t"}'),
            [{"type": "agent_start"}])
        self.assertEqual(CodexAdapter.parse_line('{"type":"turn.started"}'),
                         [{"type": "turn_start"}])
        line = ('{"type":"item.completed","item":{"id":"i0",'
                '"type":"command_execution","command":["ls","-la"],'
                '"exit_code":1,"status":"failed"}}')
        ev = CodexAdapter.parse_line(line)[0]
        self.assertEqual(ev["toolName"], "bash")
        self.assertEqual(ev["args"]["command"], "ls -la")
        self.assertTrue(ev["isError"])

    def test_parse_rollout_function_call(self):
        line = ('{"type":"response_item","payload":{"type":"function_call",'
                '"name":"exec_command","arguments":"{\\"cmd\\":[\\"git\\",'
                '\\"status\\"]}"}}')
        ev = CodexAdapter.parse_line(line)[0]
        self.assertEqual(ev["toolName"], "bash")
        self.assertEqual(ev["args"]["command"], "git status")

    def test_parse_file_change(self):
        line = ('{"type":"item.completed","item":{"type":"file_change",'
                '"changes":[{"path":"a.py"},{"path":"b.py"}],"status":"completed"}}')
        ev = CodexAdapter.parse_line(line)[0]
        self.assertEqual(ev["toolName"], "edit")
        self.assertIn("a.py", ev["args"]["path"])

    def test_resolve_adapter_by_name(self):
        self.assertIs(resolve_adapter("claude", ["claude", "-p", "x"]), ClaudeAdapter)
        self.assertIs(resolve_adapter("codex", ["codex", "exec", "x"]), CodexAdapter)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestResumeParity(unittest.TestCase):
    def test_claude_session_uuid_stable(self):
        a = ClaudeAdapter.session_uuid("ab12cd34")
        b = ClaudeAdapter.session_uuid("ab12cd34")
        self.assertEqual(a, b)
        self.assertEqual(len(a.split("-")), 5)          # UUID shape (claude demands UUID)
        self.assertNotEqual(ClaudeAdapter.session_uuid("ffffffff"), a)

    def test_claude_fresh_pins_session(self):
        out = ClaudeAdapter.build_command(["claude", "-p", "task"], session_id="ab12cd34")
        self.assertIn("--session-id", out)
        self.assertIn(ClaudeAdapter.session_uuid("ab12cd34"), out)
        self.assertNotIn("--resume", out)

    def test_claude_resume_swaps_to_resume_and_augments_prompt(self):
        out = ClaudeAdapter.build_command(["claude", "-p", "task"], session_id="ab12cd34",
                                          resume=True, feedback="PAUSE: inactivity stall")
        self.assertNotIn("--session-id", out)
        self.assertIn("--resume", out)
        self.assertIn(ClaudeAdapter.session_uuid("ab12cd34"), out)
        prompt = out[out.index("-p") + 1]
        self.assertTrue(prompt.startswith("task\n\n[edward supervisor]"))
        self.assertIn("PAUSE: inactivity stall", prompt)

    def test_codex_resume_form(self):
        out = CodexAdapter.build_command(["codex", "exec", "--json", "do it"],
                                         session_id="ab12cd34", resume=True,
                                         feedback="PAUSE: budget bleed")
        self.assertEqual(out[1:4], ["exec", "resume", "--last"])
        self.assertIn("--json", out)
        joined = " ".join(out)
        self.assertIn("[edward supervisor]", joined)
        self.assertIn("budget bleed", joined)
        self.assertIn("do it", joined)

    def test_pi_generic_accept_kwargs(self):
        from edward.adapters import PiAdapter, GenericAdapter
        self.assertEqual(GenericAdapter.build_command(["x"], resume=True, feedback="f"),
                         ["x"])
        base = PiAdapter.build_command(["pi", "task"], session_id="s", resume=True)
        self.assertIn("--session-id", base)


if __name__ == "__main__":
    unittest.main(verbosity=2)
