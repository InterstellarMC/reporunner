"""Exact-context unified patches, checked and committed as one host-side transaction."""
import re
from pathlib import Path
from ..orchestrator.aggregation import diff_paths
from ..orchestrator.models import PatchProposal, safe_path


class PatchRejected(ValueError):
    """A proposed source diff did not apply cleanly to its declared snapshot."""


def protected(path):
    parts = Path(path).parts
    return any(p in {"tests", "test", "__tests__"} for p in parts) or Path(path).name.startswith("test_") or ".test." in path or ".spec." in path or path.endswith(("_test.go", "Test.java", "Tests.cs"))


def apply_text(original: str, lines: list[str]) -> str:
    source = original.splitlines(keepends=True)
    result, cursor, i, hunks = [], 0, 0, 0
    while i < len(lines):
        match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n?", lines[i])
        if not match:
            raise ValueError("invalid unified diff hunk")
        old_start, old_count, new_start, new_count = (int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1))
        index = old_start - 1 if old_count else old_start
        if index < cursor or index > len(source):
            raise ValueError("overlapping or out-of-range hunk")
        result.extend(source[cursor:index])
        if len(result) != (new_start - 1 if new_count else new_start):
            raise ValueError("new hunk position mismatch")
        cursor = index
        old_seen = new_seen = 0
        i += 1
        while i < len(lines) and not lines[i].startswith("@@ "):
            line = lines[i]
            if line.startswith("\\ No newline at end of file"):
                raise ValueError("no-newline patches unsupported; return a newline-terminated patch")
            if not line or line[0] not in " +-":
                raise ValueError("invalid hunk line")
            content = line[1:]
            if line[0] in " -":
                if cursor >= len(source) or source[cursor] != content:
                    raise ValueError("patch context mismatch")
                old_seen += 1
                cursor += 1
            if line[0] in " +":
                result.append(content)
                new_seen += 1
            i += 1
        if (old_seen, new_seen) != (old_count, new_count):
            raise ValueError("hunk line count mismatch")
        hunks += 1
    if not hunks:
        raise ValueError("patch has no hunks")
    result.extend(source[cursor:])
    return "".join(result)


def apply_patches(root: Path, proposals: list[PatchProposal], snapshot: str):
    changes = {}
    root = root.resolve()
    for proposal in proposals:
        if proposal.snapshot != snapshot or set(proposal.files) != diff_paths(proposal.unified_diff):
            raise ValueError("patch metadata or snapshot mismatch")
        lines = proposal.unified_diff.splitlines(keepends=True)
        i = 0
        while i < len(lines):
            if lines[i].startswith("diff --git ") or lines[i].startswith("index "):
                i += 1
                continue
            if not lines[i].startswith("--- ") or i+1 >= len(lines) or not lines[i+1].startswith("+++ "):
                raise ValueError("expected unified file header")
            old, new = lines[i][4:].strip(), lines[i+1][4:].strip()
            path = safe_path((new if new != "/dev/null" else old)[2:])
            if protected(path) or path in changes:
                raise ValueError("test modifications and duplicate file patches are forbidden")
            target = root / path
            if not target.resolve().is_relative_to(root) or target.is_symlink():
                raise ValueError("patch escapes workspace")
            if target.exists() and (not target.is_file() or target.stat().st_size > 500_000):
                raise ValueError("patch target is not a bounded text file")
            if old == "/dev/null" and target.exists() or old != "/dev/null" and not target.exists():
                raise ValueError("patch file existence mismatch")
            before = target.read_text() if target.exists() else None
            j = i+2
            while j < len(lines) and not lines[j].startswith(("--- ", "diff --git ")):
                j += 1
            after = apply_text(before or "", lines[i+2:j])
            if path.endswith(".rs") and before:
                # Rust often keeps tests beside production code. Preserve the
                # test section while allowing fixes before that section.
                marker = re.search(r"#\s*\[\s*(?:cfg\s*\(\s*test\s*\)|test)\s*\]", before)
                if marker and (new == "/dev/null" or not after.endswith(before[marker.start():])):
                    raise ValueError("Rust inline test sections must remain unchanged")
            if new == "/dev/null" and after:
                raise ValueError("deletion must remove all contents")
            changes[path] = (before, None if new == "/dev/null" else after)
            i = j
    written = []
    try:
        for path, (before, after) in changes.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if after is None:
                target.unlink()
            else:
                temporary = target.with_name(target.name + ".rr-tmp")
                temporary.write_text(after)
                temporary.replace(target)
            written.append(path)
    except Exception:
        for path in written:
            before = changes[path][0]
            target = root / path
            if before is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(before)
        raise
    return changes
