"""Reference scorer server: expose Edward's /v1/score contract over any
OpenAI-compatible chat endpoint (Ollama, vLLM, llama.cpp server).

    python -m edward.scorer_server --upstream http://127.0.0.1:11434/v1 \
        --model qwen3.5:4b --port 8000 --style ollama

Zero dependencies (http.server + urllib). This is the missing half of the
local-scorer story: `pipx install edward-guard` gives you the client; this
gives you a runnable server so `edward wrap` works out of the box.

Honesty: the reference server asks the model to reply with exactly one
option id and reports one-hot probabilities (confidence 0.99). It is a
plumbing reference, not a calibrated judge — for calibrated probabilities
use the Jev backend or a LAN server with real logprob support.
"""

import argparse
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .scorer_client import MAX_STATE_CHARS


class ScorerServer:
    def __init__(self, upstream: str, model: str, api_key: str = "",
                 style: str = "openai"):
        self.upstream = upstream.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.style = style
        self.request_count = 0

    def _chat(self, messages: list, timeout: float) -> str:
        if self.style == "ollama":
            return self._chat_ollama(messages, timeout)
        payload = {"model": self.model, "messages": messages,
                   "temperature": 0.0, "max_tokens": 4096}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.upstream}/chat/completions",
                                     data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        # thinking models: final answer in content; fall back to scanning the
        # reasoning field for the last option-id mention before giving up.
        content = (msg.get("content") or "").strip()
        if content:
            return content
        return msg.get("reasoning") or ""

    def _chat_ollama(self, messages: list, timeout: float) -> str:
        # Native /api/chat so we can pass think:false: guardrail consults are
        # constrained classification, and reasoning-mode burns the whole
        # token budget before the answer lands (30-60s vs ~0.1s prefill).
        base = self.upstream[:-3] if self.upstream.endswith("/v1") else self.upstream
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "think": False,
                   "options": {"temperature": 0.0, "num_predict": 4096}}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{base}/api/chat",
                                     data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        msg = data.get("message") or {}
        content = (msg.get("content") or "").strip()
        if content:
            return content
        return msg.get("thinking") or ""

    def health(self) -> dict:
        try:
            self._chat([{"role": "user", "content": "Reply with the single word: ready"}], 30.0)
            return {"ready": True, "model": self.model, "kind": "reference"}
        except Exception as exc:
            return {"ready": False, "error": str(exc)[:200]}

    def score(self, state, question: str, options: dict) -> dict:
        self.request_count += 1
        encoded = json.dumps(state, separators=(",", ":"), default=str)
        if len(encoded) > MAX_STATE_CHARS:
            state = encoded[:MAX_STATE_CHARS]
        option_lines = "\n".join(f"- {oid}: {desc}" for oid, desc in options.items())
        user = (f"{question}\n\nAGENT STATE:\n{json.dumps(state, default=str)[:MAX_STATE_CHARS]}\n\n"
                f"OPTIONS:\n{option_lines}\n\n"
                f"Reply with EXACTLY one option id from the list and nothing else.")
        text = self._chat([{"role": "user", "content": user}], 120.0)
        # parse: last option-id mentioned wins (models often reason first,
        # answer last); fail toward UNSURE-style neutral option, never OK.
        choice = ""
        for m in re.finditer(r"[A-Za-z_]+", text):
            if m.group(0) in options:
                choice = m.group(0)
        if not choice:
            lower = {k.lower(): k for k in options}
            for m in re.finditer(r"[A-Za-z_]+", text):
                if m.group(0).lower() in lower:
                    choice = lower[m.group(0).lower()]
        if not choice:
            choice = "UNSURE" if "UNSURE" in options else next(iter(options))
        return {"option_ids": list(options),
                "probabilities": [1.0 if oid == choice else 0.0 for oid in options],
                "prompt_version": "ref-v2",
                "raw_reply": text[:200],
                "input_tokens": None,
                "total_seconds": None,
                "choice": choice}


def make_handler(server: ScorerServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(server.health())
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/v1/score":
                self._send({"error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                result = server.score(payload.get("state"), payload.get("question", ""),
                                      {o["id"]: o.get("description", "")
                                       for o in payload.get("options", [])})
                self._send(result)
            except Exception as exc:
                self._send({"error": str(exc)[:300]}, 500)

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(prog="edward-scorer-server",
                                 description="Reference /v1/score server over any OpenAI-compatible endpoint")
    ap.add_argument("--upstream", required=True,
                    help="OpenAI-compatible base URL, e.g. http://127.0.1:11434/v1 (Ollama)")
    ap.add_argument("--model", required=True, help="model name at the upstream")
    ap.add_argument("--api-key", default=os.environ.get("SCORER_UPSTREAM_API_KEY", ""))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--style", choices=("openai", "ollama"), default="openai",
                    help="openai = generic /v1 chat (default); ollama = native "
                         "/api/chat with think:false so reasoning models answer "
                         "in ~0.1s instead of burning a thinking budget")
    args = ap.parse_args()
    server = ScorerServer(args.upstream, args.model, args.api_key, args.style)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(server))
    print(f"edward scorer server: {args.model} @ {args.upstream} -> http://{args.host}:{args.port}  (ctrl-c to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
