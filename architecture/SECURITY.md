# Security boundaries

Repository text, logs, model output, and artifacts are untrusted data. Prompts label them
accordingly, but prompts are not an isolation boundary. Workers have no host filesystem or
execution tools. Code checks task identity, scope, snapshot, paths, diff hunks/counts, and conflicts.

Acquisition accepts only HTTPS GitHub owner/repository URLs. Metadata and bounded archives
are read without executing repository code on the host. Extraction rejects links, special
files, traversal, absolute paths, multiple roots, duplicate files, and excessive size/count.
Sensitive environment/key paths and caches are omitted; leaked secrets in otherwise allowed
files cannot be detected reliably by a generic filter.

Patch validation is transactional and rejects symlink escapes and original test-file changes.
Test fingerprints are checked before verification. A model's command exit status cannot
produce GREEN. The independent original test runner must observe a nonzero number of tests,
or a supported real HTTP check must pass; a fresh build and independent rerun must also pass.
Fresh builds use host-tracked source rather than copying potentially modified container state.

Candidate containers run as UID/GID 65534 with no host mounts or Docker socket, dropped
capabilities, no-new-privileges, read-only root, bounded tmpfs, 128 PID limit, a configurable RAM ceiling (2 GiB by default),
one CPU, command timeouts and bounded lifetime. The argv tool set is limited to the
supported Python, Node, Go, Rust, Maven/Java, and .NET tools and basic inspection.
Repository code executes only inside the container. Fresh compiler verification copies
the baked source and dependency caches into a bounded writable workspace; the image
root stays read-only, the user stays unprivileged, and verification networking is disabled.
The host app and Docker daemon remain trusted. Docker is not a complete kernel isolation guarantee.

Networking defaults to none. Opt-in package access uses Docker bridge networking, which
can reach host-accessible networks. It is not an Internet-only egress filter; use it only
for trusted repositories. Production hostile-code operation needs stronger isolation,
egress policy, dependency controls, multi-tenant authorization, and quotas.

The dashboard is loopback-only and checks Host and Origin. A launch-specific HTTP-only
SameSite cookie protects the local API; writes require a request guard. Model keys stay in
server memory and are never returned to the browser. The separate standalone supervisor
API uses bearer tokens; remote deployment would require managed TLS and authorization.

Provisioned values stay in execution memory and are referenced opaquely by the supervisor.
They are injected into the intended container, not prompts. Known supplied values and
common credential patterns are redacted from returned command output and cleared on cleanup.
No general secret-detection guarantee is made. Build-time private registries are unsupported;
provisioned credentials are never embedded in generated Dockerfiles.

Artifacts require the local session; HTML downloads as an attachment. Screenshots render
actual sandbox HTTP response HTML with scripts and external requests disabled; they do not
prove browser-side JavaScript behavior. SHA-256 manifests detect evidence changes but are
not signed attestations against a compromised host.

SQLite/data directories require normal account-level access controls. Recipes are trusted
local state and never bypass execution, independent verification, rebuilding, or review.
A crash can leave stopped containers for operator cleanup. Remove only containers with the
relevant run label; never prune unrelated Docker data. No remote merges or publishing occur.

Repository download, expansion, retained bytes, individual files, and member count have
separate configurable limits. Downloads, expanded archives, container input tar, and source
hashing stream in bounded chunks; archives spill to temporary disk after 8 MiB. Expanded
bytes are checked before tar/PAX metadata parsing, and member limits include directories.
Raising capacity does not remove traversal/link checks or action/time limits.
