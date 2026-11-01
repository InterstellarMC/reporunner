import io
import sys
import tarfile
import tempfile
from dataclasses import replace
import httpx
import pytest
from backend.sandbox.limits import RepositoryLimits, SandboxLimits, MIB
from backend.sandbox.workspace import extract_archive, acquire_github
from backend.sandbox.process import run


async def test_long_output_retains_final_error():
    result = await run([sys.executable, "-c", "print('a'*100000); print('FINAL FAILURE DETAIL')"])
    assert len(result.stdout) == 48_000
    assert "output truncated" in result.stdout
    assert result.stdout.endswith("FINAL FAILURE DETAIL\n")


def archive(files):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for path, content in files.items():
            info = tarfile.TarInfo("repo/" + path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return data.getvalue()


def test_larger_file_and_file_object_input(tmp_path):
    data = archive({"assets/data.bin": b"a" * (6 * MIB)})
    extract_archive(io.BytesIO(data), tmp_path)
    assert (tmp_path / "assets/data.bin").stat().st_size == 6 * MIB


@pytest.mark.parametrize("changes,match", [
    ({"expanded_bytes": 100}, "expanded archive"),
    ({"file_bytes": 2}, "repository file"),
    ({"extracted_bytes": 5}, "extracted source"),
])
def test_custom_size_boundaries_are_enforced(changes, match, tmp_path):
    with pytest.raises(ValueError, match=match):
        extract_archive(archive({"a.txt":b"1234", "b.txt":b"1234"}), tmp_path, replace(RepositoryLimits(), **changes))


@pytest.mark.parametrize("value", ["0", "-5", "unlimited"])
def test_invalid_limit_configuration_fails_clearly(value, monkeypatch):
    monkeypatch.setenv("REPORUNNER_MAX_DOWNLOAD_MB", value)
    with pytest.raises(ValueError, match="positive integer"):
        RepositoryLimits.from_env()


def test_configured_capacities_are_consistent(monkeypatch):
    monkeypatch.setenv("REPORUNNER_MAX_DOWNLOAD_MB", "384")
    assert RepositoryLimits.from_env().download_bytes == 384 * MIB
    monkeypatch.setenv("REPORUNNER_WORKSPACE_MB", "4096")
    with pytest.raises(ValueError, match="leave room"):
        SandboxLimits.from_env()


async def test_streamed_download_limit_without_content_length(tmp_path, monkeypatch):
    content = archive({"app.py": b"print('hello')\n"})
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(0, len(content), 13):
                yield content[index:index+13]
    def handle(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"sha":"a"*40})
        return httpx.Response(200, stream=Stream())
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ValueError, match="compressed repository"):
        await acquire_github("https://github.com/example/repo", tmp_path,
                             replace(RepositoryLimits(), download_bytes=len(content)-1))
    assert not list(tmp_path.iterdir())


async def test_spooled_stdin_and_output_are_drained_concurrently():
    with tempfile.SpooledTemporaryFile(max_size=1024) as data:
        data.write(b"a" * (2 * MIB))
        data.seek(0)
        result = await run([sys.executable, "-c",
            "import sys;sys.stdout.write('x'*100000);sys.stdout.flush();data=sys.stdin.buffer.read();print(len(data),file=sys.stderr)"], 5, data)
    assert result.code == 0 and len(result.stdout) == 48_000
    assert result.stderr.strip() == str(2 * MIB)
