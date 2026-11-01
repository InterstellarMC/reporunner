import asyncio
import io
import json
import hashlib
import sys
import tarfile
from pathlib import Path
import httpx
import pytest
from backend.app.server import create_application
from backend.orchestrator.models import PatchProposal
from backend.sandbox.patches import apply_patches
from backend.sandbox.process import Output, run
from backend.sandbox.workspace import Workspace, extract_archive, snapshot
from backend.sandbox.execution import LocalExecution
from backend.verifier.detect import tests_passed as passed_tests, test_command as detect_tests


def proposal(diff, files=None, snapshot_="base"):
    return PatchProposal(task_id="task", snapshot=snapshot_, files=files or ["app.py"], unified_diff=diff, reason="test")


def test_patch_applies_exactly_and_changes_snapshot(tmp_path):
    (tmp_path / "app.py").write_text("answer = '3'\n")
    before = snapshot(tmp_path)
    patch = proposal("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-answer = '3'\n+answer = 3\n")
    changes = apply_patches(tmp_path, [patch], "base")
    assert (tmp_path / "app.py").read_text() == "answer = 3\n"
    assert changes["app.py"][0] == "answer = '3'\n" and snapshot(tmp_path) != before


def test_patch_transaction_validates_all_before_writing(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    first = proposal("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")
    second = proposal("--- a/missing.py\n+++ b/missing.py\n@@ -1 +1 @@\n-a\n+b\n", ["missing.py"])
    with pytest.raises(ValueError):
        apply_patches(tmp_path, [first, second], "base")
    assert (tmp_path / "app.py").read_text() == "x = 1\n"


@pytest.mark.parametrize("diff", [
    "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-wrong\n+new\n",
    "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1 @@\n-old\n+new\n",
    "--- a/app.py\n+++ b/app.py\n@@ -8 +8 @@\n-old\n+new\n",
])
def test_invalid_hunks_are_rejected(diff, tmp_path):
    (tmp_path / "app.py").write_text("old\n")
    with pytest.raises(ValueError):
        apply_patches(tmp_path, [proposal(diff)], "base")
    assert (tmp_path / "app.py").read_text() == "old\n"


def test_stale_patch_and_original_test_modification_rejected(tmp_path):
    (tmp_path / "test_app.py").write_text("assert False\n")
    patch = proposal("--- a/test_app.py\n+++ b/test_app.py\n@@ -1 +1 @@\n-assert False\n+assert True\n", ["test_app.py"])
    with pytest.raises(ValueError):
        apply_patches(tmp_path, [patch], "wrong")
    with pytest.raises(ValueError):
        apply_patches(tmp_path, [patch], "base")


def test_new_file_and_delete(tmp_path):
    added = proposal("--- /dev/null\n+++ b/app.py\n@@ -0,0 +1 @@\n+hello\n")
    apply_patches(tmp_path, [added], "base")
    deleted = proposal("--- a/app.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-hello\n")
    apply_patches(tmp_path, [deleted], "base")
    assert not (tmp_path / "app.py").exists()


def test_patch_symlink_escape_rejected(tmp_path):
    outer = tmp_path / "outside"
    outer.mkdir()
    (outer / "app.py").write_text("old\n")
    root = tmp_path / "source"
    root.mkdir()
    (root / "app.py").symlink_to(outer / "app.py")
    with pytest.raises(ValueError):
        apply_patches(root, [proposal("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n")], "base")


def archive(entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "link":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            elif kind == "directory":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return data.getvalue()


@pytest.mark.parametrize("entries", [
    [("repo/../../escape", b"bad", "file")],
    [("/absolute/escape", b"bad", "file")],
    [("repo/link", b"", "link")],
    [("repo/a", b"1", "file"), ("repo/a", b"2", "file")],
    [("repo/a", b"1", "file"), ("other/b", b"2", "file")],
])
def test_archive_rejects_unsafe_members(entries, tmp_path):
    with pytest.raises(ValueError):
        extract_archive(archive(entries), tmp_path)


def test_archive_omits_sensitive_environment_files(tmp_path):
    extract_archive(archive([("repo/app.py", b"print('hi')\n", "file"), ("repo/.env", b"API_KEY=secret", "file")]), tmp_path)
    assert (tmp_path / "app.py").exists() and not (tmp_path / ".env").exists()


def test_archive_member_budget_includes_directories(tmp_path):
    from backend.sandbox.limits import RepositoryLimits
    entries = [(f"repo/directory-{i}", b"", "directory") for i in range(3)]
    with pytest.raises(ValueError, match="2 member limit"):
        extract_archive(archive(entries), tmp_path, RepositoryLimits(members=2))


@pytest.mark.parametrize("text,code,expected", [
    ("App started on port 8000", 0, False), ("Ran 0 tests\nOK", 0, False),
    ("Ran 1 test in 0.001s\nOK", 0, True), ("Ran 1 test\nFAILED", 1, False),
    ("3 passed in 0.1s", 0, True), ("# tests 1\n# pass 1\n# fail 0", 0, True),
    ("# tests 0\n# pass 0", 0, False),
])
def test_startup_alone_is_never_test_success(text, code, expected):
    assert passed_tests(Output(code, text, "")) is expected


async def test_subprocess_output_is_bounded_and_timeout_is_enforced():
    output = await run([sys.executable, "-c", "print('a'*100000)"], 3)
    assert output.code == 0 and len(output.stdout) <= 48000
    timed = await run([sys.executable, "-c", "import time; time.sleep(3)"], 0.02)
    assert timed.timed_out and timed.code == 124


def test_resources_are_references_and_output_is_redacted(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    e = LocalExecution(tmp_path / "runs", Path(__file__).parents[1] / "fixtures")
    workspace = Workspace("run", source, "fixture://secret")
    e.workspaces["run"] = workspace
    ref = e.provision("run", "DEMO_API_KEY", "some-nonpublic-value")
    assert ref.startswith("resource:") and "some-nonpublic-value" not in ref
    assert e.redact(workspace, "message some-nonpublic-value") == "message [REDACTED]"
    with pytest.raises(ValueError):
        e.provision("run", "PATH", "something")


async def test_full_app_session_csrf_and_loopback_boundary(tmp_path):
    app = create_application(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765") as client:
        assert (await client.get('/api/runs')).status_code == 401
        response = await client.get('/')
        assert response.status_code == 200 and 'httponly' in response.headers['set-cookie'].lower()
        assert (await client.get('/api/fixtures')).status_code == 200
        assert (await client.post('/api/runs', json={"fixture":"python-dependency"})).status_code == 403
        client.headers['X-RepoRunner']='1'
        assert (await client.post('/api/runs', json={"repository_url":"http://127.0.0.1/secret"})).status_code == 422
        assert (await client.post('/api/runs', json={"fixture":"../../outside"})).status_code == 422
        assert (await client.get('/api/runs', headers={'Origin':'https://evil.example'})).status_code == 403
        assert (await client.get('/api/runs', headers={'Host':'evil.example'})).status_code == 403


async def test_model_configuration_never_returns_keys(tmp_path):
    app = create_application(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        await client.get('/')
        response = await client.put('/api/settings', headers={'X-RepoRunner':'1'}, json={
            'supervisor_url':'http://127.0.0.1:8001/v1','supervisor_model':'supervisor','supervisor_key':'private-supervisor-value',
            'worker_url':'http://127.0.0.1:8002/v1','worker_model':'worker','worker_key':'private-worker-value'})
        assert response.status_code == 200
        result = await client.get('/api/settings')
        assert result.json()['supervisor_key_configured'] and 'private-' not in result.text


def test_repeated_evidence_packaging_keeps_valid_checksums(tmp_path):
    from backend.evidence.generator import write_bundle
    source = tmp_path / "source"
    evidence = tmp_path / "evidence"
    source.mkdir()
    evidence.mkdir()
    (source / "app.py").write_text("print('hello')\n")
    (evidence / "tests.log").write_text("Ran 1 test\nOK\n")
    ws = Workspace("run", source, "fixture://checksum")
    write_bundle(ws, evidence)
    (evidence / "result.json").write_text('{"grade":"GREEN"}')
    write_bundle(ws, evidence)
    hashes = json.loads((evidence / "checksums.json").read_text())
    assert "checksums.json" not in hashes and "evidence.zip" not in hashes
    assert all(hashlib.sha256((evidence / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())


def test_failed_preparation_still_has_a_downloadable_report(tmp_path):
    from backend.app.server import Application
    from backend.orchestrator.models import RunView
    a = Application(tmp_path)
    a.store.save(RunView(run_id="failed", status="failed", reason="archive rejected"))
    a.execution.finalize("failed", {"repository_url":"https://github.com/example/repo"})
    names = {artifact["name"] for artifact in a.details("failed")["artifacts"]}
    assert {"evidence.zip", "failure_report.md", "timeline.jsonl", "result.json"} <= names


async def test_github_metadata_fallback_uses_pinned_revision(monkeypatch, tmp_path):
    from backend.sandbox import workspace as module
    revision = "a" * 40
    content = archive([("repo/app.py", b"print('hello')\n", "file")])
    def handle(request):
        if request.url.host == "api.github.com":
            return httpx.Response(403, json={"message":"API rate limit exceeded"})
        assert str(request.url).endswith('/'+revision)
        return httpx.Response(200, content=content)
    async def metadata(*args, **kwargs):
        assert args[0][0:2] == ["git", "ls-remote"]
        return Output(0, revision + "\tHEAD\n", "")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(module, "run", metadata)
    assert await module.acquire_github("https://github.com/example/repo", tmp_path) == revision
    assert (tmp_path / "app.py").exists()


async def test_public_evaluation_records_outcomes_without_automatic_approval(monkeypatch, tmp_path):
    from argparse import Namespace
    from backend.app import cli
    manifest = tmp_path / "repositories.json"
    manifest.write_text(json.dumps({"repositories":["https://github.com/example/repo"]}))
    calls = []
    class DummyStore:
        def events(self, run_id):
            return []
    class DummyApp:
        def __init__(self, root):
            self.data, self.store = root, DummyStore()
        def launch(self, submission):
            calls.append(submission)
            return "controlled-test-run"
    async def follow(app, run_id):
        return {"run":{"status":"stopped", "grade":"RED", "reason":"resource declined", "steps":6},
                "metadata":{"elapsed_seconds":1.5}}
    monkeypatch.setattr(cli, "Application", DummyApp)
    monkeypatch.setattr(cli, "follow", follow)
    await cli.run_one(Namespace(data=str(tmp_path),command="evaluate", public=str(manifest), mode="local", network=False))
    report = json.loads(next((tmp_path / "public-evaluations").glob("*.json")).read_text())
    assert report["approval_policy"] == "interactive review per repository"
    assert report["results"][0]["grade"] == "RED" and report["results"][0]["cost_usd"] is None
    assert len(calls) == 1 and calls[0].repository_url == "https://github.com/example/repo"
