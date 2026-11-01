"""Conservative proposal validation; actual diff application belongs to Madhav."""
import re
from .models import WorkerResult, WorkerTask, safe_path


def diff_paths(diff: str) -> set[str]:
    paths = set()
    old = None
    pairs = 0
    for line in diff.splitlines():
        if line.startswith(("rename from ", "rename to ", "GIT binary patch", "Binary files ")):
            raise ValueError("binary and rename patches are unsupported")
        if line.startswith("--- "):
            old = line[4:]
            if old != "/dev/null":
                if not old.startswith("a/"):
                    raise ValueError("old diff path must use a/ prefix")
                paths.add(safe_path(old[2:]))
        elif line.startswith("+++ "):
            new = line[4:]
            if old is None:
                raise ValueError("diff missing old path")
            if new != "/dev/null":
                if not new.startswith("b/"):
                    raise ValueError("new diff path must use b/ prefix")
                paths.add(safe_path(new[2:]))
            if old == new == "/dev/null":
                raise ValueError("invalid null diff")
            if old != "/dev/null" and new != "/dev/null" and old[2:] != new[2:]:
                raise ValueError("renames unsupported")
            old = None
            pairs += 1
    if not pairs or old is not None or not re.search(r"^@@ .* @@", diff, re.M):
        raise ValueError("expected unified text diff with hunks")
    return paths


def validate_result(task: WorkerTask, result: WorkerResult, snapshot: str):
    if result.task_id != task.task_id:
        raise ValueError("worker task ID mismatch")
    if result.status == "blocked" and result.patches:
        raise ValueError("blocked workers cannot propose patches")
    for patch in result.patches:
        if patch.task_id != task.task_id or patch.snapshot != snapshot:
            raise ValueError("patch has wrong task or stale snapshot")
        if set(patch.files) != diff_paths(patch.unified_diff) or not set(patch.files) <= set(task.scope):
            raise ValueError("patch outside assigned scope or file metadata mismatch")


def conflicts(results: list[WorkerResult]) -> list[str]:
    issues, owners, findings = [], {}, {}
    for result in results:
        for patch in result.patches:
            for path in patch.files:
                if path in owners:
                    issues.append(f"overlapping file: {path}")
                owners[path] = result.task_id
        for key, value in result.findings.items():
            # Findings such as scope_compliance or repair describe different scoped
            # tasks. Compare only explicitly global runtime facts across workers.
            if key not in {"python", "python_version", "node", "node_version", "runtime", "stack"}:
                continue
            if key in findings and findings[key] != value:
                issues.append(f"contradictory finding: {key}")
            findings[key] = value
    return issues
