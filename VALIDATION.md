# Validation record

## Expanded runtimes and persistence — 1 November 2026

- 104 automated tests pass, including manifest detection, nonzero test counting, checkpoint isolation, baseline-before-model behavior, provider retries/JSON repair, token usage accumulation, and bounded output retaining final errors. Frontend JavaScript syntax passes.
- Real Docker smoke cases for static HTML, Go (with google/uuid), Rust (with itoa), Maven Java (with JUnit), and .NET (with xUnit) pass independent checks and fresh-container reruns. `benchmark/results/runtime-smoke.json` and `runtime-*.zip` contain actual results. These are small build/runtime cases, not live-model repair results or a public-repository benchmark.
- The latest original ten-fixture regression again produced seven GREEN verified repairs and three resource stops under the explicit automatic fixture evaluation policy. `benchmark/results/expanded-regression.json` and `.zip` preserve results and evidence.
- Dashboard browser checks at 1440 px and 390 px found no horizontal overflow; extended repair is selected by default. The checkpoint button and report link are present. Backend health and Docker readiness were checked locally.
- Maven fresh builds now preload test-provider dependencies by running original tests during the build before the network-disabled rerun. Compiler checks use a bounded writable workspace copied from the fresh image; no host mounts or root runtime access were added.
- .NET runtime mutexes use a writable `XDG_RUNTIME_DIR`; background: https://github.com/dotnet/runtime/issues/135726. Go checksum caches and complete dependency sums are prepared without disabling checksum verification.
- All new runtime/regression checks used no hosted model calls. General model repair quality and arbitrary-repository support are not established by these checks. Unknown toolchains, Gradle/Compose, arbitrary monorepos, system packages, and unavailable resources remain explicit limitations.

Validated locally on 31 October 2026.

- Fast automated tests: 66 passed in the final suite.
- Frontend JavaScript syntax: `node --check frontend/app.js` passed.
- Generated wire schemas and integration examples match runtime models.
- Actual Docker evaluation: 7/7 fixable local repositories passed original tests or HTTP,
  plus a fresh image and independent rerun. 3/3 deliberate resource blockers paused and
  stopped when the evaluation policy declined approval. Total recorded run time: 104.57 s.
- Actual credential/config provisioning flow: pause, provision a dummy fixture value via
  opaque reference, resume, independently test/rebuild, final QA approval. It reached GREEN;
  the supplied dummy value was absent from the timeline. No real provider credential was used.
- Browser QA: dashboard, run selection, final acceptance, evidence view, HTTP screenshot, model settings, replay import/play/pause/scrub/reset, and 390 px / 1440 px layout checks. Neither tested viewport had horizontal overflow.

The evaluator's automatic known-fixture approval is an explicit test policy, not a senior
human review. The sample secret fixture checks presence only and does not call a real API.
The original Python 3.12 image cached on this machine had a broken standard library; a
clean official Python 3.11 runtime was pulled and sanity checks were added. The actual
recorded evaluation used its pinned registry digest and the available Node 20 image.

Public repository acquisition was tested against bottlepy/bottle, including fallback from
GitHub anonymous API quota failure to Git metadata. The public Bottle run passed 363 original tests, a fresh-container rerun, and a browser QA acceptance. No source repair was needed; no public-suite success rate is claimed. A second local suite again passed all 7 fixable cases and stopped all 3 blockers. Live model inference was not tested because no
runtime API credentials/endpoints were supplied. No remote deployment, push, or competition
outcome is claimed. Actual fixture evidence is retained in benchmark/results/.

Additional packaging checks: the wheel built successfully and an isolated installation
served its bundled dashboard, ten-fixture catalog, and public manifest through the local
API. A separate real Node HTTP smoke repository passed HTTP 200 and a fresh-image HTTP
rerun; its evidence is retained in node-http-smoke.zip, outside the ten-repository suite.

## Repository capacity fix

The updated suite passes 76 tests, including configurable extraction/download boundaries,
archive expansion protection, larger files, and concurrent streamed stdin/output.
InterstellarMC/theufofiles revision a28d8e919062833ab3ef8c72c1d294264d374f49 was downloaded
and extracted successfully: 344 retained files, 48.57 MiB of source. Its two 7+ MiB data
files pass the new per-file limit. The actual source was streamed into a restricted Docker
container and the large asset was read successfully. This validates import/transfer, not
a claim that this repository's application or tests have been repaired or verified.

Current defaults are documented in README.md; historical benchmark evidence retains its
original limits and timings. The 25-action and total elapsed-time budgets are separate.
# Setup detection fix — November 1, 2026

- 80 automated tests pass after adding pyproject development/test extra installation, independent pytest detection, and matching fresh-build installation.
- The captured ShawarmaBlasters source from run `cafc01d561ab4536949dc26900911794` installed its project and pytest in an isolated Docker container with package network enabled. Its original tests passed, followed by a fresh image build and independent rerun. Evidence: `benchmark/results/shawarma-setup-fix.zip`. This check used no model and does not claim a new code repair or human acceptance.
- The controller now schedules manifest-derived installation before proposed commands and provides previous command plans to aggregation on retry. Offline package installation failures report a network blocker.
- Static web roots no longer select Python based on auxiliary helper scripts. Local recipes no longer guess a nonexistent app.py. Scoped worker findings are no longer mistaken for contradictory global runtime facts.

