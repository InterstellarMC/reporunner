import hashlib
import json
import re
import shlex
import zipfile
from pathlib import Path
from ..sandbox.workspace import source_files
from ..verifier.detect import install_command, test_command


def scrub(text):
    text = re.sub(r"(?i)(bearer\s+)[a-z0-9_.~+/-]+", r"\1[REDACTED]", text)
    return re.sub(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", text)


def dockerfile(workspace, stack, test_argv):
    lines = [f"FROM {workspace.image}", "WORKDIR /workspace", "ENV HOME=/tmp XDG_RUNTIME_DIR=/tmp PYTHONDONTWRITEBYTECODE=1 CI=true PORT=8000",
             "COPY --chown=65534:65534 . /workspace"]
    if stack == "python":
        lines.append("ENV PYTHONPATH=/opt/rr-deps:/workspace")
        install = install_command(workspace.path, stack)
        if install:
            lines.append("RUN " + shlex.join(["/opt/rr-deps" if arg == "/tmp/rr-deps" else arg for arg in install]))
    elif stack == "node":
        package = json.loads((workspace.path / "package.json").read_text()) if (workspace.path / "package.json").exists() else {}
        if package.get("dependencies") or package.get("devDependencies"):
            lines.append("RUN npm " + ("ci" if (workspace.path / "package-lock.json").exists() else "install") + " --ignore-scripts --no-audit --no-fund")
    elif stack in {"go", "rust", "java", "dotnet"}:
        cache = {"go": "GOPATH=/workspace/.rr-go GOCACHE=/tmp/go-cache GOMODCACHE=/workspace/.rr-go-mod", "rust": "CARGO_HOME=/workspace/.rr-cargo", "java": "MAVEN_CONFIG=/tmp/maven-config MAVEN_OPTS=-Dmaven.repo.local=/workspace/.m2", "dotnet": "DOTNET_CLI_HOME=/tmp DOTNET_CLI_TELEMETRY_OPTOUT=1 NUGET_PACKAGES=/workspace/.rr-nuget"}[stack]
        lines.append("ENV " + cache)
        install = install_command(workspace.path, stack)
        lines.append("RUN " + shlex.join(install))
        # Maven's go-offline goal omits dynamically selected test providers.
        # Exercise original tests during the fresh build to populate those jars,
        # then run them again in the network-disabled verification container.
        if stack == "java":
            lines.append("RUN " + shlex.join(test_command(workspace.path, stack)))
        lines.append("RUN cp -R /workspace /opt/rr-source")
        lines.append("RUN chown -R 65534:65534 /opt/rr-source")
    lines.append("RUN chown -R 65534:65534 /workspace")
    lines += ["USER 65534:65534", "CMD " + json.dumps(test_argv)]
    return "\n".join(lines) + "\n"


def write_bundle(workspace, directory, verification=None):
    if verification:
        (directory / "evidence.json").write_text(verification.model_dump_json(indent=2) + "\n")
    hashes = {}
    for path in directory.iterdir():
        if path.is_file() and path.name not in {"evidence.zip", "checksums.json"}:
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "checksums.json").write_text(json.dumps(hashes, indent=2) + "\n")
    with zipfile.ZipFile(directory / "evidence.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name != "evidence.zip":
                bundle.write(path, path.name)
        for path in source_files(workspace.path):
            bundle.write(path, "source/" + path.relative_to(workspace.path).as_posix())


async def capture_html(html, output):
    """Render actual sandbox HTML with JS and all external requests disabled."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            browser = await p.chromium.launch(headless=True, **({"executable_path": str(chrome)} if chrome.exists() else {}))
            context = await browser.new_context(viewport={"width": 1280, "height": 800}, java_script_enabled=False)
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            await page.set_content(html, wait_until="domcontentloaded")
            await page.screenshot(path=str(output), full_page=True, timeout=10000)
            await browser.close()
        return True
    except Exception:
        return False
