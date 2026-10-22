"""Minimal scoring HTTP service without an invented credit model."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._json(200, {"status": "ok", "service": "scoring"})

    def do_POST(self):
        if self.path != "/v1/score":
            self.send_error(404)
            return
        self._json(501, {"error": "not_implemented", "message": "Scoring model and decision rules are not defined"})

    def _json(self, status, data):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8001), Handler).serve_forever()
