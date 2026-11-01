"""Package editable source and captured evidence without runtime data or credentials."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
destination = root.parent / "RepoRunner-Full.zip"
skip = {".venv", ".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", ".DS_Store"}
paths = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
    and not set(p.relative_to(root).parts) & skip
    and not any(part.startswith(".reporunner") for part in p.relative_to(root).parts)
    and p.name != ".env" and p.suffix not in {".pyc", ".db", ".db-shm", ".db-wal"}]
with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(paths):
        archive.write(path, path.relative_to(root.parent))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
print(f"{destination}: {len(paths)} files, {destination.stat().st_size:,} bytes; integrity verified")
