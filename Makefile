.PHONY: setup start test evaluate contracts
setup:
	uv sync --frozen --extra test --extra screenshots
start:
	uv run --frozen --extra screenshots reporunner serve
test:
	uv run --frozen --extra test pytest -q
	node --check frontend/app.js
evaluate:
	uv run --frozen --extra screenshots reporunner evaluate
contracts:
	uv run reporunner-contracts
