#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  echo "Install uv first, then run this launcher again. See README.md."
  exit 1
fi
echo "Start Docker Desktop, then open http://127.0.0.1:8765"
exec uv run --frozen --extra screenshots reporunner serve
