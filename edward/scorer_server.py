"""Reference scorer server: expose Edward's /v1/score contract over any
OpenAI-compatible chat endpoint (Ollama, vLLM, llama.cpp server).

    python -m edward.scorer_server --upstream http://127.0.0.1:11434/v1 \
        --model qwen3.5:4b --port 8000 --style ollama

Zero dependencies (http.server + urllib). This is the missing half of the
local-scorer story: `pipx install edward-guard` gives you the client; this
gives you a runnable server so `edward wrap` works out of the box.

Honesty: with --style openai the reference server asks the model to reply with
one option id and reports one-hot probabilities. With --style ollama it runs a
SemIf-style direct readout instead (TheoLeeCJ/SemIf, direct-options): raw Qwen
ChatML with an empty think block, one forward pass, num_predict=1, and the
answer distribution taken from the top-20 next-token logprobs over the option
letters A.. — softmaxed in float64. Probabilities are then real (conditional on
the supplied options), so downstream confidence gates see calibrated-ish
values, not a fake 1.0. It is still a plumbing reference, not a trained
decision model (see jaredpalmer/kev for that).
"""

import argparse
import json
import math
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .scorer_client import MAX_STATE_CHARS

# SemIf direct system prompt (byte-faithful to SemIf core.py DIRECT_SYSTEM).
SEMIF_SYSTEM = ("Apply the supplied criterion to the supplied evidence. "
                "Choose exactly one listed option. Respond with only its "
                "uppercase letter, with no explanation or reasoning.")
# Raw Qwen ChatML with an empty think block: the prompt ends exactly at the
# first answer-token position, so one forward pass reads the decision.
SEMIF_TEMPLATE = ("<|im_start|>system\n{system}<|im_end|>\n"
                  "<|im_start|>user\n{user}<|im_end|>\n"
                  "<|im_start|>assistant\n<think>\n\n</think>\n\n")


class ScorerServer:
    def __init__(self, upstream: str, model: str, api_key: str = "",
                 style: str = "openai", post_fn=None):
        self.upstream = upstream.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.style = style
        self.request_count = 0
        base = self.upstream[:-3] if self.upstream.endswith("/v1") else self.upstream
        self.ollama_base = base
        self.post_fn = post_fn or self._post_json

    @staticmethod
    def _post_json(url, payload, headers, timeout):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

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


    def health(self) -> dict:
        try:
            if self.style == "ollama":
                self._score_ollama_direct({}, "Reply ready.", {"OK": "ready"})
            elif self.style == "vllm":
                self._score_vllm_direct({}, "Reply ready.", {"OK": "ready"})
            else:
                self._chat([{"role": "user", "content": "Reply with the single word: ready"}], 30.0)
            return {"ready": True, "model": self.model, "kind": "reference"}
        except Exception as exc:
            return {"ready": False, "error": str(exc)[:200]}

    def score(self, state, question: str, options: dict) -> dict:
        self.request_count += 1
        if self.style == "ollama":
            return self._score_ollama_direct(state, question, options)
        if self.style == "vllm":
            return self._score_vllm_direct(state, question, options)
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

    def _score_ollama_direct(self, state, question: str, options: dict) -> dict:
        """SemIf-style direct readout over Ollama /api/generate.

        One forward pass, no generation: the prompt is raw Qwen ChatML with an
        empty think block, ending exactly at the answer position; the decision
        distribution comes from the top-20 next-token logprobs over the option
        letters, softmaxed in float64. Letters missing from the top-20 are
        floored just under the weakest observed candidate (documented
        deviation: SemIf/semantic-if reject such rows; a reference server
        should stay total and note the substitution)."""
        evidence = json.dumps(state, ensure_ascii=False, separators=(", ", ": "),
                              default=str)[:MAX_STATE_CHARS] if state else "(none)"
        payload = {"evidence": evidence, "criterion": question,
                   "options": [{"letter": chr(65 + i), "description": desc}
                               for i, (oid, desc) in enumerate(options.items())
                               if i < 16]}
        user = json.dumps(payload, ensure_ascii=False)
        prompt = SEMIF_TEMPLATE.format(system=SEMIF_SYSTEM, user=user)
        body = {"model": self.model, "prompt": prompt, "raw": True, "stream": False,
                "logprobs": True, "top_logprobs": 20,
                "options": {"temperature": 0.0, "num_predict": 1}}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = self.post_fn(f"{self.ollama_base}/api/generate", body,
                            headers, 120.0)
        pos = (data.get("logprobs") or [{}])[0]
        argmax_token = pos.get("token") or ""
        letters = {chr(65 + i): oid for i, oid in enumerate(options) if i < 16}
        found = {}
        for t in pos.get("top_logprobs") or []:
            tok = (t.get("token") or "").strip()
            if tok in letters and tok not in found:
                found[tok] = float(t.get("logprob") or 0.0)
        if not found:
            neutral = "UNSURE" if "UNSURE" in options else next(iter(options))
            probs = {oid: (1.0 if oid == neutral else 0.0) for oid in options}
            note = "direct: no option letter in top-20, neutral fallback"
        else:
            floor = min(found.values()) - 4.6
            values = {L: found.get(L, floor) for L in letters}
            mx = max(values.values())
            exps = {L: math.exp(v - mx) for L, v in values.items()}
            total = math.fsum(exps.values())
            probs = {letters[L]: exps[L] / total for L in letters}
            note = (f"direct argmax={argmax_token!r} "
                    + " ".join(f"{letters[L]}={values[L]:.3f}" for L in letters))
        return {"option_ids": list(options),
                "probabilities": [probs.get(oid, 0.0) for oid in options],
                "prompt_version": "semif-direct-v1",
                "raw_reply": note[:200],
                "input_tokens": data.get("prompt_eval_count"),
                "total_seconds": None,
                "choice": max(probs, key=probs.get) if probs else
                          ("UNSURE" if "UNSURE" in options else next(iter(options)))}

    def _letter_token_ids(self, n_options: int) -> dict:
        """Token id per option letter via the upstream /tokenize endpoint
        (vLLM OpenAI-compatible extension; lives at the server root in
        vLLM 0.31, under /v1 in some versions — try both)."""
        ids = {}
        for i in range(n_options):
            letter = chr(65 + i)
            data = None
            for url in (f"{self.ollama_base}/tokenize", f"{self.upstream}/tokenize"):
                try:
                    data = self.post_fn(url,
                                        {"model": self.model, "prompt": letter,
                                         "add_special_tokens": False},
                                        {"Content-Type": "application/json"}, 30.0)
                    break
                except Exception:
                    continue
            toks = (data or {}).get("tokens") or (data or {}).get("token_ids") or []
            if len(toks) == 1:
                ids[letter] = toks[0]
        return ids

    def _score_vllm_direct(self, state, question: str, options: dict) -> dict:
        """SemIf-style direct readout over vLLM's OpenAI-compatible server.

        One raw /v1/completions call with the ChatML prompt ending at the
        answer position; allowed_token_ids pins sampling to the option
        letters, logprobs=20 returns the raw next-token distribution for the
        softmax. Letters missing from the top-k are fetched with a follow-up
        request constrained to that single letter (semantic-if's trick) —
        no flooring needed, every value is raw."""
        evidence = json.dumps(state, ensure_ascii=False, separators=(", ", ": "),
                              default=str)[:MAX_STATE_CHARS] if state else "(none)"
        payload = {"evidence": evidence, "criterion": question,
                   "options": [{"letter": chr(65 + i), "description": desc}
                               for i, (oid, desc) in enumerate(options.items())
                               if i < 16]}
        user = json.dumps(payload, ensure_ascii=False)
        prompt = SEMIF_TEMPLATE.format(system=SEMIF_SYSTEM, user=user)
        letters = {chr(65 + i): oid for i, oid in enumerate(options) if i < 16}
        token_ids = self._letter_token_ids(len(letters))
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        def completion(allow_ids):
            body = {"model": self.model, "prompt": prompt, "max_tokens": 1,
                    "temperature": 0.0, "logprobs": 20, "stream": False,
                    "allowed_token_ids": allow_ids}
            data = self.post_fn(f"{self.upstream}/completions", body,
                                headers, 120.0)
            choice0 = (data.get("choices") or [{}])[0]
            lp = choice0.get("logprobs") or {}
            rows = lp.get("top_logprobs") or []
            row = rows[0] if rows else {}
            if isinstance(row, dict) and row:
                # vLLM completions: {token_str: logprob}
                return choice0.get("text") or "", dict(row)
            # OpenAI-style [{"token":..., "logprob":...}]
            flat = {r.get("token"): r.get("logprob") for r in row
                    if isinstance(r, dict)}
            return choice0.get("text") or "", flat

        sampled, row = completion(list(token_ids.values()))
        found = {}
        for tok, val in row.items():
            key = (tok or "").strip()
            if key in letters and key not in found:
                found[key] = float(val)
        # follow-up for letters outside the top-k (constrained single-token)
        for letter in letters:
            if letter in found:
                continue
            if letter not in token_ids:
                continue
            _, lrow = completion([token_ids[letter]])
            for tok, val in lrow.items():
                if (tok or "").strip() == letter:
                    found[letter] = float(val)
                    break
        if not found:
            neutral = "UNSURE" if "UNSURE" in options else next(iter(options))
            probs = {oid: (1.0 if oid == neutral else 0.0) for oid in options}
            note = "vllm direct: no option letter in top-k, neutral fallback"
        else:
            mx = max(found.values())
            exps = {L: math.exp(found[L] - mx) for L in found}
            total = math.fsum(exps.values())
            probs = {letters[L]: exps[L] / total for L in found}
            note = (f"vllm direct sampled={sampled.strip()!r} "
                    + " ".join(f"{L}={found[L]:.3f}" for L in sorted(found)))
        return {"option_ids": list(options),
                "probabilities": [probs.get(oid, 0.0) for oid in options],
                "prompt_version": "semif-direct-v1",
                "raw_reply": note[:200],
                "input_tokens": None,
                "total_seconds": None,
                "choice": max(probs, key=probs.get) if probs else
                          ("UNSURE" if "UNSURE" in options else next(iter(options)))}


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
    ap.add_argument("--style", choices=("openai", "ollama", "vllm"), default="openai",
                    help="openai = generic /v1 chat (default); ollama = native "
                         "/api/chat direct readout (think off); vllm = raw "
                         "/v1/completions with allowed_token_ids — the strongest "
                         "direct readout (every probability is a raw logprob)")
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
