import argparse
import asyncio
import json
import getpass
from pathlib import Path
from .server import Application, ROOT, DEFAULT_DATA, Submission
from ..orchestrator.models import HumanApproval


async def follow(app, run_id, json_events=False):
    print("Run:", run_id)
    after = 0
    while run_id in app.jobs:
        for event in app.store.events(run_id, after):
            after = event.sequence
            print(event.model_dump_json() if json_events else f"[{after:03}] {event.type}")
        controller = app.controllers.get(run_id)
        pending = controller.approvals.get(run_id) if controller else None
        if pending and not pending[1].done():
            print("\nReview the evidence:", app.execution.evidence_dir(run_id))
            view = app.store.view(run_id)
            if view.verification and view.verification.passes():
                answer = await asyncio.to_thread(input, "Accept this verified candidate? [y/N] ")
                controller.approve(run_id, HumanApproval(approval_id=pending[0], approved=answer.lower() == "y"))
            elif view.approval_kind == "review":
                answer = await asyncio.to_thread(input, "Acknowledge this planning review? [y/N] ")
                controller.approve(run_id, HumanApproval(approval_id=pending[0], approved=answer.lower() == "y"))
            else:
                name = await asyncio.to_thread(input, "Resource variable name (leave empty to stop): ")
                refs = []
                if name:
                    value = await asyncio.to_thread(getpass.getpass, "Resource value (hidden): ")
                    refs = [app.execution.provision(run_id, name, value)]
                controller.approve(run_id, HumanApproval(approval_id=pending[0], approved=bool(refs), resource_refs=refs))
        await asyncio.sleep(0.2)
    result = app.details(run_id)
    print(json.dumps(result, indent=2))
    return result


async def run_one(args):
    from uuid import uuid4
    app = Application(Path(args.data))
    if args.command == "evaluate":
        benchmark_id = uuid4().hex
        if not args.public:
            await app.evaluate(benchmark_id)
            print((app.data / "benchmarks" / (benchmark_id + ".json")).read_text())
            return
        urls = json.loads(Path(args.public).read_text())["repositories"]
        if not isinstance(urls, list) or not 1 <= len(urls) <= 35 or not all(isinstance(u, str) for u in urls):
            raise ValueError("public manifest must contain 1–35 repository URL strings")
        report = {"id": benchmark_id, "source": "public GitHub repositories", "status": "running",
            "approval_policy": "interactive review per repository", "mode": args.mode, "results": []}
        folder = app.data / "public-evaluations"
        folder.mkdir(exist_ok=True)
        destination = folder / (benchmark_id + ".json")
        for url in urls:
            run_id = app.launch(Submission(repository_url=url, mode=args.mode, network=args.network))
            details = await follow(app, run_id)
            tokens = sum(e.data.get("usage", {}).get("total_tokens", 0) for e in app.store.events(run_id) if e.type == "model.completed")
            report["results"].append({"repository": url, "run_id": run_id, "status": details["run"]["status"],
                "grade": details["run"]["grade"], "reason": details["run"]["reason"], "steps": details["run"]["steps"],
                "seconds": details["metadata"]["elapsed_seconds"], "reported_tokens": tokens or None, "cost_usd": None})
            destination.write_text(json.dumps(report, indent=2) + "\n")
        report["status"] = "completed"
        destination.write_text(json.dumps(report, indent=2) + "\n")
        print("Public evaluation report:", destination)
        return
    run_id = app.launch(Submission(repository_url=args.url or "", fixture=args.fixture, mode=args.mode, network=args.network,
        resume_from=args.resume, max_steps=args.steps, max_seconds=args.seconds))
    await follow(app, run_id, args.json)


def main():
    parser = argparse.ArgumentParser(description="RepoRunner — real sandbox repair and evidence")
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Open the local dashboard")
    serve.add_argument("--port", type=int, default=8765)
    run = sub.add_parser("run", help="Run a repository interactively")
    run.add_argument("url", nargs="?")
    run.add_argument("--fixture")
    run.add_argument("--mode", choices=["local", "live"], default="local")
    run.add_argument("--network", action="store_true", help="Enable sandbox network for package downloads (trusted repositories only)")
    run.add_argument("--json", action="store_true")
    run.add_argument("--resume", help="Continue from a previous run ID's candidate source")
    run.add_argument("--steps", type=int, default=25, help="Action budget, up to 250")
    run.add_argument("--seconds", type=int, default=600, help="Time budget, up to 3600 seconds")
    evaluate = sub.add_parser("evaluate", help="Evaluate local fixtures or a public repository manifest")
    evaluate.add_argument("--public", nargs="?", const=str(ROOT / "benchmark/public_repositories.json"), help="Public repository JSON manifest (interactive approval; default bundled proposed set)")
    evaluate.add_argument("--mode", choices=["local", "live"], default="live", help="Public evaluation engine; local fixtures always use local recipes")
    evaluate.add_argument("--network", action="store_true", help="Enable package downloads for trusted public evaluation repositories")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from .server import create_application
        print(f"\nRepoRunner → http://127.0.0.1:{args.port}\n")
        uvicorn.run(create_application(Path(args.data)), host="127.0.0.1", port=args.port)
    else:
        try:
            asyncio.run(run_one(args))
        except (ValueError, OSError) as exc:
            parser.exit(2, f"RepoRunner: {exc}\n")
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                parser.exit(2, f"RepoRunner: {exc.detail}\n")
            raise


if __name__ == "__main__":
    main()
