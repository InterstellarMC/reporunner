"""Real, deterministic repair recipes. No simulated LLM calls or execution evidence."""
import asyncio
import difflib
import json
import re
from .models import Command, Decision, Decomposition, PatchProposal, WorkerResult, WorkerTask


class LocalProvider:
    name = "Local recipe engine (no LLM)"

    async def generate(self, instruction, payload, output):
        await asyncio.sleep(0)
        if output is Decomposition:
            context = payload["context"]
            files = list(context["files"])
            feedback = payload.get("feedback", "")
            return Decomposition(tasks=[
                WorkerTask(task_id="repair", specialist="dependency" if "module" in feedback.lower() else "build",
                    objective=("Repair observed failure:\n" + feedback[-2200:]) if feedback else "Analyze setup without making speculative changes.",
                    scope=files[:20] or ["README.md"], acceptance_criteria=["Small evidence-based fix; preserve original tests"]),
                WorkerTask(task_id="environment", specialist="environment", objective="Assess runtime and independent test strategy.",
                    scope=[p for p in files if p in {"package.json", "requirements.txt", "pyproject.toml", "README.md"}][:10] or files[:1] or ["README.md"],
                    acceptance_criteria=["Identify runtime from actual manifests"])])
        if output is WorkerResult:
            task, files = payload["task"], payload["files"]
            patches, findings, evidence = [], {}, []
            if task["task_id"] == "environment":
                findings = {"runtime": payload.get("environment", {}).get("stack", "detected from repository")}
                evidence = ["Runtime selected from source manifests; tests run independently in Docker."]
            else:
                feedback = task["objective"]
                replacements = self.repairs(files, feedback)
                for path, after in replacements.items():
                    before = files[path]
                    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                        fromfile="a/"+path, tofile="b/"+path))
                    if diff:
                        patches.append(PatchProposal(task_id=task["task_id"], snapshot=payload["snapshot"],
                            files=[path], unified_diff=diff, reason="Local recipe matched observed error and repository evidence"))
                findings = {"repair": "Proposed a minimal fix from a matching recipe" if patches else "No speculative code change"}
                evidence = ["Observed failure matched a local recipe" if patches else "No matching failure or no supported repair found"]
            return WorkerResult(task_id=task["task_id"], status="completed", findings=findings, evidence=evidence, patches=patches)
        if output is Decision:
            environment = payload.get("environment", {})
            stack = environment.get("stack", "python")
            commands = []
            # Requirements may have just gained a local wheel; always install an existing
            # requirements file. pip handles an empty/comment-only file without network.
            if environment.get("install_argv"):
                commands.append(Command(argv=environment["install_argv"], timeout_seconds=90))
            elif environment.get("has_requirements"):
                commands.append(Command(argv=["python", "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                    "--no-cache-dir", "--target", "/tmp/rr-deps", "-r", "requirements.txt"], timeout_seconds=90))
            argv = environment.get("test_argv")
            # Exercise a declared Node test script first; independent verification uses the
            # original native test runner if that is the repository's test framework.
            if stack == "node" and environment.get("has_test_script"):
                argv = ["npm", "test"]
            if argv:
                commands.append(Command(argv=argv))
            elif not environment.get("server_argv"):
                return Decision(human_reason="No supported original tests or application entry point found. Configure a test/start command for this repository.")
            return Decision(accepted_task_ids=[r["task_id"] for r in payload["results"] if r["status"] == "completed"],
                commands=commands, rationale="Run actual setup and tests; use only supported error-to-fix recipes.")
        raise ValueError("unsupported local engine output")

    def repairs(self, files, feedback):
        changes = {}
        # Missing Python import with an explicitly advertised, vendored wheel.
        missing = re.search(r"No module named ['\"]([^'\"]+)", feedback)
        if missing:
            name = missing[1].split(".")[0]
            req = files.get("requirements.txt", "")
            wheel = re.search(r"vendor/" + re.escape(name) + r"-[A-Za-z0-9_.-]+\.whl", req)
            if wheel and ("./" + wheel[0]) not in req.splitlines():
                changes["requirements.txt"] = req + ("" if req.endswith("\n") or not req else "\n") + "./" + wheel[0] + "\n"
            else:
                candidates = {p[:-3]: p for p in files if p.endswith(".py") and "/" not in p}
                match = difflib.get_close_matches(name, candidates, n=1, cutoff=0.7)
                if match:
                    for path, content in files.items():
                        if path.endswith(".py") and not path.startswith("test"):
                            after = re.sub(r"\b(from|import) " + re.escape(name) + r"\b", lambda m: m[1] + " " + match[0], content)
                            if after != content:
                                changes[path] = after
        # Only convert a named configuration key after an actual type error identifies it.
        key = re.search(r"config\.([a-zA-Z_][a-zA-Z0-9_]*) must be (?:an? )?(?:integer|number)", feedback)
        if key and "config.json" in files:
            config = json.loads(files["config.json"])
            value = config.get(key[1])
            if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
                config[key[1]] = int(value)
                changes["config.json"] = json.dumps(config, indent=2) + "\n"
        # Repair an explicitly missing Node relative import when an unambiguous sibling exists.
        node_missing = re.search(r"Cannot find module ['\"]\./([^'\"]+)", feedback)
        if node_missing:
            name = node_missing[1].removesuffix(".js")
            candidates = [p[:-3] for p in files if p.endswith(".js") and "/" not in p]
            match = difflib.get_close_matches(name, candidates, n=1, cutoff=0.7)
            if match:
                for path, text in files.items():
                    if path.endswith(".js") and not path.startswith("test"):
                        after = text.replace("'./" + node_missing[1] + "'", "'./" + match[0] + "'").replace('"./' + node_missing[1] + '"', '"./' + match[0] + '"')
                        if after != text:
                            changes[path] = after
        if "package.json" in files and ("Could not find" in feedback or "Cannot find module" in feedback or "could not find" in feedback.lower()):
            if any("node:test" in text for path, text in files.items() if path.endswith(".js")):
                package = json.loads(files["package.json"])
                if package.get("scripts", {}).get("test") != "node --test":
                    package.setdefault("scripts", {})["test"] = "node --test"
                    changes["package.json"] = json.dumps(package, indent=2) + "\n"
        return changes
