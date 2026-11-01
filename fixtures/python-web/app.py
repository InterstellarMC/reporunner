import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
config = json.loads(Path(__file__).with_name("config.json").read_text())
if not isinstance(config["port"], int):
    raise TypeError("config.port must be integer")
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body style='background:#10231d;color:#d9f9b1;font:24px sans-serif;padding:80px'><p>ASTROWORLD / REPORUNNER</p><h1>This repository is running.</h1><p>Actual HTTP 200. Fresh container. Auditable evidence.</p></body></html>")
HTTPServer(("0.0.0.0", config["port"]), Handler).serve_forever()
