"""RepoRunner CLI entrypoint.

Usage:
    python -m reporunner          # splash screen
    python -m reporunner --help   # full help
    python -m reporunner run ...  # submit a run
"""

import sys

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    from .cli import app
    app(argv)  # type: ignore[arg-type]


if __name__ == "__main__":
    sys.exit(main())
