# Evaluation

`results/local-2026-10-31.json` contains recorded measurements from **real Docker runs**
of ten deliberately broken local repositories. These fixtures are not public GitHub
repositories and the results are not a general success-rate estimate.

The seven repairable cases passed the original test suite or actual HTTP 200 probe, then
passed a fresh image build and independent rerun. The three deliberately unavailable
resources caused approval pauses and were declined by the fixture evaluation policy.
The recorded sum of run times was 104.57 seconds. A rerun can differ.

The evaluator automatically accepts verified known-fixture candidates and declines resource
blockers. This is an explicit test policy, **not a human senior developer review**. Normal
dashboard and CLI runs always require an interactive final approval.

Each fixture has its complete source in `fixtures/` and a captured evidence archive in
`results/<fixture>.zip`. Original logs, source changes, Dockerfile, RUN.md, verification,
and replay are retained. SHA-256 checksums cover the evidence files, excluding the checksum
file and archive itself.

To measure a fresh local run:

```sh
uv run --extra screenshots reporunner evaluate
```

Or use **Benchmarks → Run 10-repository evaluation** in the dashboard. Actual results
persist under `.reporunner/benchmarks/`; each run's evidence is under `.reporunner/runs/`.

`public_repositories.json` is a proposed public test set, with **no claimed scores**.
Use `reporunner run <url> --mode live` after configuring model endpoints. Network access
is disabled by default; enable it only for trusted repositories that need registry downloads.
Record blocked/failed cases as well as successful cases. Public API quotas and source
changes make this a different experiment from the deterministic local suite.

## Public and repeat-run checks

`results/public-bottle-2026-10-31.json` and `public-bottle.zip` record the public Bottle
repository run. Its 363 original tests passed, as did the fresh-container rerun. No source
repair was necessary. Final acceptance was exercised by the development assistant during
browser QA; this is not represented as an independent senior human review.

`results/local-warm-2026-10-31.json` records another complete local evaluation after the
final controller changes: again 7 verified and 3 blocked. Its recorded time sum was 85.37 s.
The dependency case used 9 actions versus 13 in the original run; verification/rebuild and
review were still performed. Wall time also depends on caching and concurrent activity.

Run the proposed public suite interactively (configured endpoints required for Live AI):

```sh
uv run reporunner evaluate --public --mode live --network
```

Use `--public /path/to/manifest.json` for another JSON object with a repositories array,
maximum 35 URLs. Each public candidate requires interactive review; no public repository
is auto-approved. Reports persist under `.reporunner/public-evaluations/`, including failures,
actions, time, and model-reported token counts. Cost stays null without pricing information.

`node-http-smoke.json` / `node-http-smoke.zip` capture a separate real Node HTTP
validation fixture. It passed an actual HTTP probe and fresh-image HTTP rerun using PORT=8000.
It is not an extra row in the ten-repository benchmark.
