"""Only trusted host tools (Docker) are invoked here; repository code runs in containers."""
import asyncio
import os
from dataclasses import dataclass
from typing import BinaryIO


@dataclass
class Output:
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False


async def run(argv: list[str], timeout=60, stdin: bytes | BinaryIO | None = None, extra_env=None) -> Output:
    process = await asyncio.create_subprocess_exec(*argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(extra_env or {})})

    async def drain(stream):
        head, tail = bytearray(), bytearray()
        total = 0
        marker = b"\n[... output truncated; beginning and ending retained ...]\n"
        head_limit, tail_limit = 16_000, 32_000 - len(marker)
        while chunk := await stream.read(8192):
            total += len(chunk)
            take = min(len(chunk), head_limit-len(head))
            head.extend(chunk[:take])
            tail.extend(chunk[take:])
            if len(tail) > 32_000:
                del tail[:-32_000]
        data = head + (marker + tail[-tail_limit:] if total > 48_000 else tail)
        return bytes(data).decode(errors="replace")

    async def feed():
        if stdin is not None:
            try:
                if isinstance(stdin, bytes):
                    chunks = (stdin[i:i+65536] for i in range(0, len(stdin), 65536))
                else:
                    chunks = iter(lambda: stdin.read(65536), b"")
                for chunk in chunks:
                    process.stdin.write(chunk)
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

    async def communicate():
        _, out, err = await asyncio.gather(feed(), drain(process.stdout), drain(process.stderr))
        return Output(await process.wait(), out, err)

    job = asyncio.create_task(communicate())
    try:
        return await asyncio.wait_for(asyncio.shield(job), timeout)
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.kill()
        result = await job
        return Output(124, result.stdout, result.stderr + "\nCommand exceeded its time limit.", True)
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await asyncio.gather(job, return_exceptions=True)
        raise
