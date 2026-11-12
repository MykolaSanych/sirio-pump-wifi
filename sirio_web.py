"""Dev server for the redesigned Sirio web UI.

Serves webui/app/ on localhost AND reverse-proxies /ajax/* to the real WiNET
module, so the page runs same-origin (no CORS) against the live pump while we
design it. Later the same index.html goes onto the module itself.

  python sirio_web.py                      # module 192.168.0.107, http://localhost:8080
  python sirio_web.py --module 192.168.0.107 --port 8080
"""
import argparse
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "app")
MODULE = "192.168.0.107"
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
         ".svg": "image/svg+xml", ".ico": "image/x-icon", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _proxy(self, method):
        url = f"http://{MODULE}{self.path}"
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=method)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                enc = r.headers.get("Content-Encoding")
                if enc:
                    self.send_header("Content-Encoding", enc)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except Exception as e:
            self.send_error(502, f"proxy to module failed: {e}")

    def do_POST(self):
        if self.path.startswith("/ajax/"):
            self._proxy("POST")
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path.startswith("/ajax/"):
            self._proxy("GET")
            return
        rel = self.path.split("?")[0].lstrip("/") or "index.html"
        fp = os.path.normpath(os.path.join(APP_DIR, rel))
        if not fp.startswith(APP_DIR) or not os.path.isfile(fp):
            self.send_error(404)
            return
        data = open(fp, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(os.path.splitext(fp)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    global MODULE
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default=MODULE)
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    MODULE = args.module
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving {APP_DIR}")
    print(f"proxy /ajax/* -> http://{MODULE}")
    print(f"open http://localhost:{args.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
