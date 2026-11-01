"""Small real repositories for offline evaluation; not public-repo benchmark claims."""
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def wheel():
    files = {"greeting/__init__.py": 'def greet(name):\n    return f"Hello, {name}!"\n',
        "greeting-1.0.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: greeting\nVersion: 1.0.0\n",
        "greeting-1.0.0.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: RepoRunner fixture builder\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    rows = []
    for path, value in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()).rstrip(b"=").decode()
        rows.append(f"{path},sha256={digest},{len(value.encode())}")
    files["greeting-1.0.0.dist-info/RECORD"] = "\n".join(rows) + "\ngreeting-1.0.0.dist-info/RECORD,,\n"
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, value in files.items():
            info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, value)
    return result.getvalue()


def create(name, title, description, files, blocked=False):
    root = ROOT / "fixtures" / name
    root.mkdir(parents=True, exist_ok=True)
    files["README.md"] = f"# {title}\n\n{description}\n\nThis is a small, deliberately broken local evaluation repository.\n"
    if any(path.endswith(".py") for path in files) and "requirements.txt" not in files:
        files["requirements.txt"] = "# This example uses the Python standard library.\n"
    for name_, content in files.items():
        p = root / name_
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content) if isinstance(content, bytes) else p.write_text(content)
    return {"id": name, "title": title, "description": description, "stack": "node" if "package.json" in files else "python", "expected": "blocked" if blocked else "fixable", "source": "curated local fixture"}


def main():
    entries = []
    py_test = 'import unittest\nfrom app import greet\n\nclass GreetingTest(unittest.TestCase):\n    def test_greeting(self):\n        self.assertEqual(greet("team"), "Hello, team!")\n'
    entries.append(create("python-dependency", "The missing dependency", "A greeting library is vendored as a wheel, but requirements.txt never installs it.", {
        "app.py": "from greeting import greet\n", "tests/test_app.py": py_test,
        "requirements.txt": "# Offline wheel available: vendor/greeting-1.0.0-py3-none-any.whl\n",
        "vendor/greeting-1.0.0-py3-none-any.whl": wheel()}))
    entries.append(create("python-import", "One letter away", "The import points at greeting; the actual local module is greetings.py.", {
        "app.py": "from greeting import greet\n", "greetings.py": 'def greet(name):\n    return f"Hello, {name}!"\n', "tests/test_app.py": py_test}))
    entries.append(create("python-config", "A number in disguise", "Configuration stores a multiplier as text, and the original test rejects it.", {
        "config.json": '{\n  "multiplier": "3"\n}\n',
        "app.py": 'import json\nfrom pathlib import Path\ndef multiply(value):\n    config = json.loads(Path(__file__).with_name("config.json").read_text())\n    if not isinstance(config["multiplier"], int):\n        raise TypeError("config.multiplier must be integer")\n    return value * config["multiplier"]\n',
        "tests/test_app.py": 'import unittest\nfrom app import multiply\nclass MultiplyTest(unittest.TestCase):\n    def test_multiply(self):\n        self.assertEqual(multiply(3), 9)\n'}))
    entries.append(create("python-web", "The server that won't start", "A mistyped port prevents an HTTP app from binding. Verification must probe HTTP, then rebuild.", {
        "config.json": '{\n  "port": "8000"\n}\n',
        "app.py": 'import json\nfrom pathlib import Path\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\nconfig = json.loads(Path(__file__).with_name("config.json").read_text())\nif not isinstance(config["port"], int):\n    raise TypeError("config.port must be integer")\nclass Handler(BaseHTTPRequestHandler):\n    def do_GET(self):\n        self.send_response(200)\n        self.send_header("Content-Type", "text/html")\n        self.end_headers()\n        self.wfile.write(b"<html><body style=\'background:#10231d;color:#d9f9b1;font:24px sans-serif;padding:80px\'><p>ASTROWORLD / REPORUNNER</p><h1>This repository is running.</h1><p>Actual HTTP 200. Fresh container. Auditable evidence.</p></body></html>")\nHTTPServer(("0.0.0.0", config["port"]), Handler).serve_forever()\n'}))
    package = json.dumps({"name": "reporunner-node-fixture", "version": "1.0.0", "private": True, "scripts": {"test": "node --test"}}, indent=2) + "\n"
    node_test = "const test = require('node:test');\nconst assert = require('node:assert/strict');\nconst {multiply} = require('../app');\ntest('multiplier is applied', () => assert.equal(multiply(3),9));\n"
    entries.append(create("node-config", "Strictly a number", "Node's original tests detect a configuration value with the wrong type.", {
        "package.json": package, "config.json": '{\n  "multiplier": "3"\n}\n',
        "app.js": "const config = require('./config.json');\nexports.multiply = value => {\n  if (typeof config.multiplier !== 'number') throw new TypeError('config.multiplier must be number');\n  return value * config.multiplier;\n};\n", "tests/app.test.js": node_test}))
    entries.append(create("node-import", "A missing sibling", "A relative Node import has a typo; the correct source file is already in the repository.", {
        "package.json": package, "app.js": "module.exports = require('./multipler');\n",
        "multiplier.js": "exports.multiply = value => value * 3;\n", "tests/app.test.js": node_test}))
    wrong = json.loads(package)
    wrong["scripts"]["test"] = "node tests/old-runner.js"
    entries.append(create("node-test-script", "Yesterday's test command", "The package script references a removed runner; real native Node tests still exist.", {
        "package.json": json.dumps(wrong, indent=2) + "\n", "app.js": "exports.multiply = value => value * 3;\n", "tests/app.test.js": node_test}))
    for name, title, description, app in [
        ("python-secret", "Ask, don't invent", "The repository needs an API key. The agent must ask a human.", 'import os\ndef check():\n    if not os.environ.get("DEMO_API_KEY"):\n        raise RuntimeError("missing API key: DEMO_API_KEY")\n    return True\n'),
        ("python-gpu", "Hardware is a real boundary", "This repository requires an unavailable GPU capability.", 'import shutil\ndef check():\n    if not shutil.which("nvidia-smi"):\n        raise RuntimeError("CUDA unavailable: GPU hardware required")\n    return True\n'),
        ("python-private", "A private dependency", "The repository needs an unavailable private package; the agent must escalate.", 'def check():\n    try:\n        import private_customer_sdk\n    except ImportError:\n        raise RuntimeError("private package unavailable: private_customer_sdk")\n    return True\n')]:
        entries.append(create(name, title, description, {"app.py": app,
            "tests/test_app.py": 'import unittest\nfrom app import check\nclass CapabilityTest(unittest.TestCase):\n    def test_required_capability(self):\n        self.assertTrue(check())\n'}, blocked=True))
    (ROOT / "fixtures" / "catalog.json").write_text(json.dumps(entries, indent=2) + "\n")


if __name__ == "__main__":
    main()
