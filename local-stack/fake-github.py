"""Fake GitHub REST API for `make chaos`: answers the calls the worker's GitHub tools make for
repo acme/payments, makes the search slow (SEARCH_DELAY_SECONDS) so a worker can be killed
mid-call, and counts every request. Standard library only.

  GET  /_requests  {"search": n, "add_label": n, "total": n}   (add_label = POSTs that add a label)
  POST /_reset     clears the counts
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = "acme/payments"
DELAY = float(os.environ.get("SEARCH_DELAY_SECONDS", "8"))
counts = {"search": 0, "add_label": 0, "total": 0}
lock = threading.Lock()

SEARCH = {
    "total_count": 1,
    "items": [
        {
            "number": 7,
            "title": "Refunds fail for EUR cards",
            "state": "open",
            "labels": [],
            "html_url": f"https://github.com/{REPO}/issues/7",
            "repository_url": f"https://api.github.com/repos/{REPO}",
        }
    ],
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/_requests":
            with lock:
                return self.reply(200, dict(counts))
        self.count(None)
        if path == "/search/issues":
            self.count("search")
            time.sleep(DELAY)
            return self.reply(200, SEARCH)
        if path == f"/repos/{REPO}/labels/bug":
            return self.reply(200, {"name": "bug"})
        if path == f"/repos/{REPO}/labels":
            return self.reply(200, [{"name": "bug"}])
        self.reply(404, {"message": "Not Found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if path == "/_reset":
            with lock:
                counts.update(search=0, add_label=0, total=0)
            return self.reply(200, dict(counts))
        self.count(None)
        if path == f"/repos/{REPO}/issues/42/labels":
            self.count("add_label")
            return self.reply(200, [{"name": "bug"}])
        self.reply(404, {"message": "Not Found"})

    def count(self, name):
        with lock:
            counts[name or "total"] += 1

    def reply(self, status, body):
        data = json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the worker was killed while waiting: expected during chaos

    def log_message(self, fmt, *args):
        print(f"{self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}", flush=True)


if __name__ == "__main__":
    print(f"fake GitHub on :8099 (search delay {DELAY}s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
