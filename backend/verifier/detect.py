import json
import re
import tomllib
from pathlib import Path
from ..sandbox.workspace import source_files


def test_command(root: Path, stack: str):
    paths = list(source_files(root))
    if stack == "go" and any(p.name.endswith("_test.go") for p in paths):
        return ["go", "test", "-v", "./..."]
    if stack == "rust":
        return ["cargo", "test", "--offline"]
    if stack == "java" and (root / "pom.xml").exists():
        return ["mvn", "-B", "-Dmaven.repo.local=/workspace/.m2", "test"]
    if stack == "dotnet":
        return ["dotnet", "test", "--no-restore", "--verbosity", "normal"]
    if stack == "python":
        tests = [p for p in paths if p.name.startswith("test_") and p.suffix == ".py"]
        if not tests:
            return []
        pytest_style = any(re.search(r"^def test_|\bimport pytest\b|\bfrom pytest\b", p.read_text(errors="replace"), re.M) for p in tests)
        if pytest_style:
            return ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        directory = "tests" if (root / "tests").is_dir() else "."
        return ["python", "-m", "unittest", "discover", "-s", directory, "-v"]
    if stack == "node":
        tests = [p for p in paths if p.suffix in {".js", ".mjs", ".cjs", ".ts"} and (".test." in p.name or ".spec." in p.name or "test" in p.relative_to(root).parts or "tests" in p.relative_to(root).parts)]
        if not tests:
            return []
        if any("node:test" in p.read_text(errors="replace") for p in tests):
            return ["node", "--test"]
        package = json.loads((root / "package.json").read_text()) if (root / "package.json").exists() else {}
        if "test" in package.get("scripts", {}):
            return ["npm", "test"]
    return []


def tests_passed(output):
    if output.code or output.timed_out:
        return False
    text = output.stdout + "\n" + output.stderr
    return bool(re.search(r"Ran [1-9]\d* tests?\b|[1-9]\d* passed\b|# (?:tests|pass) [1-9]\d*\b|✔ tests [1-9]\d*\b|Tests:\s+[1-9]\d* passed|--- PASS:|Tests run: [1-9]\d*, Failures: 0, Errors: 0|Passed:\s+[1-9]\d*", text))


def server_command(root, stack):
    if stack == "python" and (root / "app.py").exists():
        text = (root / "app.py").read_text()
        if any(s in text for s in ("HTTPServer", "Flask(", "uvicorn.run", "app.run(")):
            return ["python", "app.py"]
    if stack == "node":
        package = json.loads((root / "package.json").read_text()) if (root / "package.json").exists() else {}
        if "start" in package.get("scripts", {}):
            return ["npm", "start"]
        if (root / "index.html").is_file() and not (root / "package.json").exists():
            return ["node", "-e", "const http=require('http'),fs=require('fs'),path=require('path');const root=process.cwd();http.createServer((q,s)=>{try{const p=path.resolve(root,'.'+decodeURIComponent(new URL(q.url,'http://localhost').pathname));if(!p.startsWith(root+path.sep)&&p!==root){s.writeHead(403);return s.end();}const file=fs.statSync(p).isDirectory()?path.join(p,'index.html'):p;const mime={'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml'};s.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');s.end(fs.readFileSync(file));}catch(e){s.writeHead(404);s.end('Not found');}}).listen(8000,'0.0.0.0');"]
    return []


def install_command(root, stack):
    if stack == "go":
        return ["go", "mod", "download", "all"]
    if stack == "rust":
        return ["cargo", "fetch"]
    if stack == "java":
        return ["mvn", "-B", "-Dmaven.repo.local=/workspace/.m2", "dependency:go-offline"]
    if stack == "dotnet":
        return ["dotnet", "restore"]
    if stack == "python":
        args = []
        req = root / "requirements.txt"
        if req.exists() and any(l.strip() and not l.lstrip().startswith("#") for l in req.read_text().splitlines()):
            args += ["-r", "requirements.txt"]
        project = root / "pyproject.toml"
        if project.exists():
            data = tomllib.loads(project.read_text())
            metadata = data.get("project", {})
            extras = metadata.get("optional-dependencies", {})
            selected = [name for name in ("dev", "test", "tests", "testing") if name in extras]
            if metadata.get("name"):
                args.append(".[" + ",".join(selected) + "]" if selected else ".")
            else:
                args.extend(metadata.get("dependencies", []))
                for name in selected:
                    args.extend(extras[name])
        if "pytest" in test_command(root, stack):
            args.append("pytest")
        if args:
            return ["python", "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                    "--no-cache-dir", "--target", "/tmp/rr-deps", *args]
    if stack == "node":
        package = json.loads((root / "package.json").read_text()) if (root / "package.json").exists() else {}
        if package.get("dependencies") or package.get("devDependencies"):
            return ["npm", "ci" if (root / "package-lock.json").exists() else "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    return []
