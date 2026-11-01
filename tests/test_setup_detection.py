from types import SimpleNamespace
from backend.verifier.detect import install_command
from backend.evidence.generator import dockerfile
from backend.sandbox.workspace import context_for
from backend.orchestrator.aggregation import conflicts
from backend.orchestrator.models import WorkerResult


def test_pyproject_test_extras_are_installed_in_candidate_and_rebuild(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="sample"\nversion="1.0"\n[project.optional-dependencies]\ndev=["pytest>=7"]\n')
    (tmp_path / "test_sample.py").write_text('def test_sample(): assert True\n')
    command = install_command(tmp_path, "python")
    assert ".[dev]" in command and "pytest" in command
    recipe = dockerfile(SimpleNamespace(path=tmp_path, image="python:3.11-slim"), "python", ["python", "-m", "pytest"])
    assert ".[dev]" in recipe and "/opt/rr-deps" in recipe
    assert "/tmp/rr-deps" not in recipe


def test_pytest_detected_without_requirements(tmp_path):
    (tmp_path / "test_sample.py").write_text('import pytest\n')
    assert install_command(tmp_path, "python")[-1] == "pytest"


def test_static_web_root_does_not_select_python_helper_scripts(tmp_path):
    (tmp_path / "index.html").write_text('<h1>Web app</h1>')
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/build.py").write_text('print("build")')
    assert context_for(tmp_path, "fixture://web").stack == "node"


def test_scoped_findings_are_not_global_contradictions():
    results = [WorkerResult(task_id="a", status="completed", findings={"scope_compliance": "true"}),
               WorkerResult(task_id="b", status="completed", findings={"scope_compliance": "false"})]
    assert not conflicts(results)
