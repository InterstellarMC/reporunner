# Agent behavior

The runtime supervisor is a configured high-capability model; the developer assistant
used to build this project is unrelated to that runtime model. Open-source specialists
can run locally or at hosted chat-compatible inference endpoints.

| Specialist | Narrow responsibility |
| --- | --- |
| repository | Entry points, layout, setup evidence |
| environment | Python/Node/runtime and OS compatibility |
| dependency | Missing packages and version conflicts |
| configuration | Runtime configuration and service prerequisites |
| build | Build and startup failures |
| test | Diagnosis of actual failing checks |

The supervisor receives bounded setup files, the latest error tail, its previous decision,
an error category, and a strategy-change flag. It returns tasks with exact file scope and
acceptance criteria. Only scoped file contents go to each worker. Workers return findings,
evidence, unresolved questions, and small snapshot-bound unified diffs. No worker has a
filesystem or execution tool.

The aggregator reviews results and conflicts and selects compatible completed task IDs.
The controller revalidates the selected subset. Stale, mismatched, sensitive-path, or
out-of-scope proposals cannot reach execution. Full diff application checks remain the
execution service's responsibility. Overlapping files are rejected conservatively.

Errors activate a new decomposition with category hints: dependency, environment,
configuration, build, or test. Missing credentials, GPUs, and private packages cause human
provisioning approval. The repeated-error fingerprint normalizes numeric noise and temporary
paths. On a second occurrence the supervisor must choose another strategy; an unchanged
command and patch plan is rejected (snapshot IDs and explanatory wording do not count
as a strategy change). A third occurrence stops the run. This
check cannot prove that a different patch is semantically a better strategy.

Recipe memory keys include all supplied file contents plus the normalized failure. The
last successful plan is cached only after current-snapshot verification and final approval.
A new run with identical context/error can skip model calls and replay the validated plan,
but must execute it, verify independently, rebuild, and get final approval again. Recipes
are suggestions bound to exact context, not claims that future execution will succeed.

Unexpected provider errors fail closed with sanitized exception class names. Calls are
not retried invisibly, which keeps budget use and failures visible to the operator.

## Local deterministic engine

LocalProvider implements a separate, labelled no-LLM path: narrowly scoped known error
recipes for vendored wheels, misspelled local imports, numeric config types, and stale native
Node test scripts. It returns the same structured contracts and must pass the identical
real execution, verification, rebuild, and approval gates. It does not claim general model
reasoning. Model endpoints are used only in Live AI mode.
