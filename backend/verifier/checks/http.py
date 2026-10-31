"""HTTP web application verification checks."""

from __future__ import annotations

import time
from typing import Optional
from backend.sandbox.api.schemas import CheckResult, CheckVerdict
from backend.verifier.ports import CommandRunner


def run_http_check(
    runner: CommandRunner,
    port: int = 5000,
    path: str = "/",
    start_cmd: Optional[str] = None,
    timeout_s: int = 60,
) -> CheckResult:
    """
    Verify web application by probing port and path inside the container.
    Returns CheckResult with PASS if 2xx/3xx received, or FAIL/TIMEOUT/BLOCKED.
    """
    start_time = time.monotonic()

    probe_script = f"""
import time, urllib.request, sys
port = {port}
path = '{path}'
timeout = {timeout_s}
start = time.time()
status_code = None
error_msg = ''

while time.time() - start < timeout:
    try:
        url = f'http://127.0.0.1:{{port}}{{path}}'
        req = urllib.request.Request(url, headers={{'User-Agent': 'RepoRunnerVerifier/1.0'}})
        with urllib.request.urlopen(req, timeout=3) as resp:
            status_code = resp.status
            if 200 <= status_code < 400:
                print(f'HTTP {{status_code}} OK')
                sys.exit(0)
    except urllib.error.HTTPError as e:
        status_code = e.code
        if 200 <= status_code < 400:
            print(f'HTTP {{status_code}} OK')
            sys.exit(0)
        error_msg = f'HTTP error: {{e.code}}'
    except Exception as e:
        error_msg = str(e)
    time.sleep(1)

print(f'HTTP Probe failed: {{error_msg}} (last status: {{status_code}})')
sys.exit(1)
"""
    import base64
    b64 = base64.b64encode(probe_script.encode("utf-8")).decode("ascii")
    cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64}').decode('utf-8'))\""

    code, out, err = runner.run(cmd, timeout_s=timeout_s + 5)
    dur = time.monotonic() - start_time

    if code == 0:
        return CheckResult(
            check_type="http_probe",
            verdict=CheckVerdict.PASS,
            output=f"HTTP probe on port {port}{path} passed ({out.strip()})",
            duration_s=round(dur, 2),
        )

    # Check if blocked by secret/auth or failure
    combined = f"{out}\n{err}"
    if "Connection refused" in combined:
        verdict = CheckVerdict.FAIL
        msg = f"Server on port {port} refused connection within {timeout_s}s"
    elif "401" in combined or "403" in combined:
        verdict = CheckVerdict.BLOCKED
        msg = f"HTTP probe returned authentication error on port {port}{path}"
    else:
        verdict = CheckVerdict.FAIL
        msg = f"HTTP probe failed on port {port}{path}:\n{combined}"

    return CheckResult(
        check_type="http_probe",
        verdict=verdict,
        output=msg,
        duration_s=round(dur, 2),
    )
