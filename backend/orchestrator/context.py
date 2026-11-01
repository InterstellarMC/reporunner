import hashlib
from pathlib import Path
from .models import RepositoryContext, safe_path


def build_context(root: Path, repository_url: str, snapshot: str) -> RepositoryContext:
    """Read bounded setup files only. Never execute the repository or follow symlinks."""
    root = root.resolve()
    names = {"README.md", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
             "package.json", "Dockerfile", "Makefile", "pytest.ini", "tsconfig.json"}
    files = {}
    total = 0
    for path in sorted(root.iterdir()):
        if path.name not in names or path.is_symlink() or not path.is_file():
            continue
        safe_path(path.name)
        if path.stat().st_size > 30_000:
            continue
        content = path.read_text(errors="replace")
        total += len(content.encode())
        if total > 200_000:
            break
        files[path.name] = content
    stack = "node" if "package.json" in files else "python" if set(files) & {"pyproject.toml", "requirements.txt", "setup.py"} else "unknown"
    return RepositoryContext(repository_url=repository_url, snapshot=snapshot, files=files, stack=stack)


def context_key(context: RepositoryContext) -> str:
    # Reuse only when the complete supplied source context matches, independent of commit ID.
    import json
    return hashlib.sha256(json.dumps(context.files, sort_keys=True).encode()).hexdigest()
