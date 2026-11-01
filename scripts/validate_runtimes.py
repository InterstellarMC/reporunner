"""Real Docker smoke checks, without model calls or a repair-success claim.

Run: uv run python -m scripts.validate_runtimes [node go rust java dotnet]
"""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4
from backend.sandbox.execution import LocalExecution
from backend.orchestrator.models import ActionRequest, Command
from backend.verifier.detect import install_command

CASES = {
    "node": {"index.html": "<!doctype html><h1>RepoRunner static check</h1>"},
    "go": {"go.mod": "module example.com/smoke\n\ngo 1.23\nrequire github.com/google/uuid v1.6.0\n", "sum.go": "package smoke\nfunc Sum(a,b int) int { return a+b }\n", "sum_test.go": 'package smoke\nimport ("testing"; "github.com/google/uuid")\nfunc TestSum(t *testing.T) { if Sum(2,3)!=5 || uuid.Nil.String()=="" { t.Fatal("sum") } }\n'},
    "rust": {"Cargo.toml": '[package]\nname="rr_smoke"\nversion="0.1.0"\nedition="2021"\n[dependencies]\nitoa="1"\n', "src/lib.rs": "pub fn sum(a:i32,b:i32)->i32 { a+b }\n", "tests/sum.rs": "#[test]\nfn sum() { assert_eq!(rr_smoke::sum(2,3),5); assert_eq!(itoa::Buffer::new().format(5),\"5\"); }\n"},
    "java": {"pom.xml": '<project><modelVersion>4.0.0</modelVersion><groupId>example</groupId><artifactId>smoke</artifactId><version>1.0</version><properties><maven.compiler.source>17</maven.compiler.source><maven.compiler.target>17</maven.compiler.target></properties><dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency></dependencies></project>', "src/main/java/Sum.java": "public class Sum { public static int sum(int a,int b) { return a+b; } }", "src/test/java/SumTest.java": "import org.junit.Test; import static org.junit.Assert.*; public class SumTest { @Test public void sum() { assertEquals(5, Sum.sum(2,3)); } }"},
    "dotnet": {"Smoke.csproj": '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework><IsTestProject>true</IsTestProject></PropertyGroup><ItemGroup><PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1"/><PackageReference Include="xunit" Version="2.9.2"/><PackageReference Include="xunit.runner.visualstudio" Version="2.8.2"/></ItemGroup></Project>', "SumTests.cs": "using Xunit; public class SumTests { [Fact] public void Sum() { Assert.Equal(5,2+3); } }"},
}


async def main():
    root = Path(tempfile.mkdtemp(prefix="rr-runtime-validation-"))
    ex = LocalExecution(root / "runs", root / "fixtures")
    output = Path("benchmark/results")
    output.mkdir(parents=True, exist_ok=True)
    summary = output / "runtime-smoke.json"
    results = json.loads(summary.read_text())["results"] if summary.exists() else []
    for stack in sys.argv[1:] or CASES:
        rid = uuid4().hex
        for name, content in CASES[stack].items():
            path = root / "fixtures" / stack / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        print("Checking", stack, flush=True)
        try:
            context = await ex.prepare(rid, fixture=stack, network=True)
            argv = install_command(ex.workspaces[rid].path, stack)
            if argv:
                installed = await ex.execute(ActionRequest(run_id=rid, action_id="setup", snapshot=context.snapshot, patches=[],
                    commands=[Command(argv=argv, timeout_seconds=300)], remaining_seconds=600))
                if installed.exit_code:
                    raise RuntimeError(installed.stderr[-2000:])
                context = installed.context
            verification = await ex.verify(rid, context.snapshot, 600)
            result = {"runtime": stack, "passed": verification.passes(), "checks": [c.model_dump() for c in verification.checks], "diagnostic": verification.diagnostic}
            shutil.copyfile(ex.evidence_dir(rid) / "evidence.zip", output / f"runtime-{stack}.zip")
        except Exception as error:
            result = {"runtime": stack, "passed": False, "error": str(error)[-2000:]}
        finally:
            await ex.close(rid)
            if (ex.evidence_dir(rid) / "evidence.zip").exists():
                shutil.copyfile(ex.evidence_dir(rid) / "evidence.zip", output / f"runtime-{stack}.zip")
        results = [previous for previous in results if previous['runtime'] != stack] + [result]
        (output / "runtime-smoke.json").write_text(json.dumps({"scope": "Small local Docker smoke cases; no model calls, code repairs, or human acceptance", "results": results}, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    print("Working directory:", root, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
