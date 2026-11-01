import hashlib
import gzip
import io
import json
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
import httpx
from ..orchestrator.models import RepositoryContext, safe_path
from .process import run
from .limits import RepositoryLimits, MIB
from .runtimes import detect_stack

IGNORED = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".DS_Store"}


def source_files(root):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if set(rel.parts) & IGNORED or p.is_symlink() or not p.is_file():
            continue
        try:
            safe_path(rel.as_posix())
        except ValueError:
            continue
        yield p


def snapshot(root):
    digest = hashlib.sha256()
    for p in source_files(root):
        digest.update(p.relative_to(root).as_posix().encode() + b"\0")
        with p.open("rb") as content:
            while chunk := content.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def context_for(root, url):
    files, total = {}, 0
    candidates = list(source_files(root))
    priority = {"README.md", "requirements.txt", "package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml", "app.py", "app.js", "config.json"}
    candidates.sort(key=lambda p: (p.name not in priority, len(p.relative_to(root).parts), str(p)))
    for p in candidates:
        if p.suffix not in {".py", ".js", ".ts", ".go", ".rs", ".java", ".cs", ".xml", ".csproj", ".sln", ".html", ".css", ".json", ".toml", ".md", ".txt", ".yml", ".yaml"} and p.name not in {"Dockerfile", "Makefile", "go.mod", "go.sum", "Cargo.lock"}:
            continue
        if p.stat().st_size > 30_000 or len(files) >= 80:
            continue
        try:
            data = p.read_text()
        except UnicodeDecodeError:
            continue
        if total + len(data.encode()) > 180_000:
            continue
        total += len(data.encode())
        files[p.relative_to(root).as_posix()] = data
    stack = detect_stack(root)
    return RepositoryContext(repository_url=url, snapshot=snapshot(root), stack=stack, files=files)


@dataclass
class Workspace:
    run_id: str
    path: Path
    repository_url: str
    network: bool = False
    fixture: str | None = None
    container: str | None = None
    image: str = ""
    history: list = field(default_factory=list)
    actions: dict = field(default_factory=dict)
    test_argv: list[str] = field(default_factory=list)
    initial_tests: dict = field(default_factory=dict)
    resources: dict = field(default_factory=dict, repr=False)
    resource_refs: set = field(default_factory=set, repr=False)

    def context(self):
        return context_for(self.path, self.repository_url)


def extract_archive(data, destination: Path, limits: RepositoryLimits | None = None):
    limits = limits or RepositoryLimits.from_env()
    total, count = 0, 0
    prefix, seen = None, set()
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    source.seek(0)
    # Bound expansion before parsing PAX metadata; spool large archives to disk.
    with tempfile.SpooledTemporaryFile(max_size=8 * MIB) as raw:
        expanded = 0
        with gzip.GzipFile(fileobj=source, mode="rb") as compressed:
            while chunk := compressed.read(min(MIB, limits.expanded_bytes - expanded + 1)):
                expanded += len(chunk)
                if expanded > limits.expanded_bytes:
                    raise ValueError(f"expanded archive exceeds configured {limits.expanded_bytes / MIB:g} MiB limit (REPORUNNER_MAX_ARCHIVE_MB)")
                raw.write(chunk)
        raw.seek(0)
        with tarfile.open(fileobj=raw, mode="r:") as archive:
            for member in archive:
                count += 1
                if count > limits.members:
                    raise ValueError(f"repository archive exceeds configured {limits.members:,} member limit (REPORUNNER_MAX_MEMBERS)")
                parts = Path(member.name).parts
                if Path(member.name).is_absolute() or "\\" in member.name:
                    raise ValueError("absolute archive paths are forbidden")
                if parts:
                    prefix = prefix or parts[0]
                    if parts[0] != prefix:
                        raise ValueError("archive must contain one repository root")
                if len(parts) < 2:
                    continue
                path = "/".join(parts[1:])
                if not path:
                    continue
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ValueError("repository archives must not contain links or special files")
                try:
                    safe_path(path)
                except ValueError:
                    if path.startswith(".env") or path.endswith((".pem", ".key")):
                        continue
                    raise
                if set(Path(path).parts) & IGNORED:
                    continue
                target = destination / path
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("archive path escaped destination")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += member.size
                if path in seen:
                    raise ValueError("duplicate archive file")
                seen.add(path)
                if member.size > limits.file_bytes:
                    raise ValueError(f"repository file {path!r} exceeds configured {limits.file_bytes / MIB:g} MiB limit (REPORUNNER_MAX_FILE_MB)")
                if total > limits.extracted_bytes:
                    raise ValueError(f"extracted source exceeds configured {limits.extracted_bytes / MIB:g} MiB limit (REPORUNNER_MAX_SOURCE_MB)")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as src:
                    with target.open("wb") as output:
                        shutil.copyfileobj(src, output, length=1024 * 1024)


async def acquire_github(url: str, destination: Path, limits: RepositoryLimits | None = None):
    limits = limits or RepositoryLimits.from_env()
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", url)
    if not match or any(p in {".", ".."} for p in match.groups()):
        raise ValueError("use a public https://github.com/owner/repository URL")
    owner, repo = match.groups()
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        response = await client.get(f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD",
            headers={"Accept": "application/vnd.github+json"})
        sha = response.json().get("sha", "") if response.status_code == 200 else ""
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            # GitHub's anonymous REST quota is often exhausted on shared event Wi-Fi.
            # ls-remote reads metadata only; it never checks out or executes repository code.
            metadata = await run(["git", "ls-remote", f"https://github.com/{owner}/{repo}.git", "HEAD"], 20,
                                 extra_env={"GIT_TERMINAL_PROMPT": "0"})
            match_sha = re.search(r"^([0-9a-f]{40})\s+HEAD$", metadata.stdout, re.M)
            sha = match_sha[1] if match_sha else "HEAD"
        with tempfile.SpooledTemporaryFile(max_size=8 * MIB) as archive:
            digest, length = hashlib.sha256(), 0
            async with client.stream("GET", f"https://codeload.github.com/{owner}/{repo}/tar.gz/{sha}") as download:
                download.raise_for_status()
                declared = download.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > limits.download_bytes:
                    raise ValueError(f"compressed repository exceeds configured {limits.download_bytes / MIB:g} MiB limit (REPORUNNER_MAX_DOWNLOAD_MB)")
                async for chunk in download.aiter_bytes(chunk_size=64 * 1024):
                    length += len(chunk)
                    if length > limits.download_bytes:
                        raise ValueError(f"compressed repository exceeds configured {limits.download_bytes / MIB:g} MiB limit (REPORUNNER_MAX_DOWNLOAD_MB)")
                    archive.write(chunk)
                    digest.update(chunk)
            archive.seek(0)
            extract_archive(archive, destination, limits)
    return sha if sha != "HEAD" else "archive-sha256:" + digest.hexdigest()
