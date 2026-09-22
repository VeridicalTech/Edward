"""Human approval loop for PAUSE decisions.

On PAUSE (when policy.wait_approval_seconds > 0), Edward sends a Slack
message (via the configured incoming webhook) carrying one-time decision
links backed by a token, and serves a tiny local HTTP endpoint for the
callback:

    http://<host>:<port>/resume?token=...   -> respawn the agent (--continue)
    http://<host>:<port>/kill?token=...     -> terminate, exit 76

Binds 127.0.0.1 by default; set approval_host 0.0.0.0 only on trusted LANs.
No Slack bot token is required — an existing incoming webhook is enough.
"""

import http.server
import secrets
import threading
import time


class ApprovalServer:
    def __init__(self, port: int = 8765, host: str = "127.0.0.1",
                 timeout_seconds: int = 300):
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.token = secrets.token_urlsafe(16)
        self.decision = None  # "resume" | "kill"
        self._server = None
        self._thread = None

    def urls(self) -> dict:
        base = f"http://{self.host}:{self.port}"
        return {"resume": f"{base}/resume?token={self.token}",
                "kill": f"{base}/kill?token={self.token}"}

    def start(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if f"token={server.token}" not in self.path:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"invalid token")
                    return
                if self.path.startswith("/resume"):
                    server.decision = "resume"
                    body = b"Edward: resuming agent session."
                elif self.path.startswith("/kill"):
                    server.decision = "kill"
                    body = b"Edward: agent terminated."
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # silence default stderr logging
                pass

        self._server = http.server.ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait(self) -> str:
        """Block until a decision arrives or the timeout expires.
        Returns "resume" | "kill" | "timeout"."""
        deadline = time.time() + self.timeout_seconds
        while self.decision is None and time.time() < deadline:
            time.sleep(0.5)
        self.stop()
        return self.decision or "timeout"

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
