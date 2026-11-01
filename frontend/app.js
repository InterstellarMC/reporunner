const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const escape = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  mode: "local",
  fixtures: [],
  runs: [],
  selected: null,
  details: null,
  events: [],
  tab: "logs",
  live: false,
  replay: null,
  replayTimer: null,
  page: "runs",
  patchText: "",
  patchRun: null,
  busy: false,
};
let toastTimer;
function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", error);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 6500);
}
async function api(path, method = "GET", data) {
  const response = await fetch("/api" + path, {
    method,
    headers: {
      "X-RepoRunner": "1",
      ...(data ? { "Content-Type": "application/json" } : {}),
    },
    ...(data ? { body: JSON.stringify(data) } : {}),
  });
  if (!response.ok) {
    let error;
    try {
      error = await response.json();
    } catch {
      error = { detail: response.statusText };
    }
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : JSON.stringify(error.detail),
    );
  }
  return response.json();
}
function time(value) {
  try {
    return new Date(value).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}
function runName(details) {
  const m = details.metadata;
  return (
    state.fixtures.find((f) => f.id === m.fixture)?.title ||
    m.repository_url?.replace(/\/$/, "").split("/").slice(-2).join("/") ||
    m.fixture ||
    details.run.run_id.slice(0, 10)
  );
}
function elapsed(d) {
  if (!d) return "0s";
  const m = d.metadata;
  const sec = m.finished_at
    ? m.elapsed_seconds
    : Math.max(0, (Date.now() - Date.parse(m.started_at)) / 1000);
  return sec >= 60
    ? `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`
    : `${Math.floor(sec || 0)}s`;
}
function friendly(type) {
  return (
    {
      "run.queued": "Run queued",
      "run.started": "Analysis started",
      "round.started": "Refinement round",
      "plan.created": "Specialist tasks assigned",
      "model.completed": "Engine response received",
      "worker.started": "Specialist working",
      "worker.completed": "Specialist findings returned",
      "aggregation.conflict": "Conflicting proposals detected",
      "aggregation.accepted": "Plan accepted",
      "execution.started": "Sandbox command started",
      "execution.completed": "Command completed",
      "execution.output": "Actual command output",
      "verification.started": "Independent verification started",
      "verification.completed": "Verification results returned",
      "rebuild.started": "Building a fresh image",
      "rebuild.completed": "Fresh-container check completed",
      "patch.applied": "Source patch applied",
      "approval.requested": "Human review requested",
      "approval.received": "Human decision received",
      "memory.hit": "Verified recipe found",
      "sandbox.ready": "Isolated sandbox ready",
      "sandbox.creating": "Creating Docker sandbox",
      "sandbox.closed": "Sandbox cleaned up",
      "run.completed": "Verified candidate accepted",
      "run.stopped": "Run stopped",
      "run.failed": "Run failed",
      "budget.step": "Action budget charged",
      "cleanup.failed": "Sandbox cleanup needs attention",
      "repository.downloading": "Downloading repository",
      "repository.resolved": "Git commit resolved",
    }[type] || type
  );
}
function eventDetail(e) {
  const d = e.data;
  return (
    d.reason ||
    d.rationale ||
    (d.argv ? d.argv.join(" ") : null) ||
    (d.category
      ? `Category: ${d.category}${d.strategy_change ? " · new strategy required" : ""}`
      : null) ||
    (d.task ? d.task.objective : null) ||
    (d.task_id
      ? `${d.task_id}${d.patch_count ? ` · ${d.patch_count} patch proposal(s)` : ""}`
      : null) ||
    (d.image ? d.image : null) ||
    (d.files ? d.files.join(", ") : null) ||
    (d.issues ? d.issues.join("; ") : null) ||
    (d.result
      ? d.result.checks
          .map((c) => `${c.passed ? "PASS" : "FAIL"} ${c.name}`)
          .join(" · ")
      : null) ||
    (d.operation ? d.operation : null) ||
    (d.passed !== undefined ? (d.passed ? "Passed" : "Failed") : null) ||
    (d.approved !== undefined
      ? d.approved
        ? "Approved"
        : "Declined"
      : null) ||
    ""
  );
}

async function health() {
  try {
    const h = await api("/health");
    state.live = h.live_configured;
    const el = $("#docker-status");
    el.className =
      "system-status " + (h.docker.available ? "online" : "offline");
    el.innerHTML = `<i></i>${h.docker.available ? "Docker online" : "Docker unavailable"}`;
    el.title = h.docker.reason || `Docker ${h.docker.version}`;
    $("#live-config-dot").textContent = state.live ? "●" : "○";
  } catch (e) {
    $("#docker-status").className = "system-status offline";
    $("#docker-status").innerHTML = "<i></i> Server disconnected";
  }
}
async function history() {
  try {
    state.runs = await api("/runs");
    $("#run-count").textContent = state.runs.length;
    $("#history").innerHTML = state.runs.length
      ? state.runs
          .map(
            (d) =>
              `<button class="history-item ${state.selected === d.run.run_id ? "active" : ""}" data-run="${escape(d.run.run_id)}"><span class="history-dot ${["running", "awaiting_approval"].includes(d.run.status) ? "running" : escape(d.run.grade)}"></span><div><strong>${escape(runName(d))}</strong><small>${escape(d.metadata.mode === "live" ? "LIVE AI" : "LOCAL RECIPES")} · ${escape(d.run.status.replaceAll("_", " "))}</small></div></button>`,
          )
          .join("")
      : '<p class="sidebar-empty">Your first run starts here.</p>';
  } catch (e) {
    toast(e.message, true);
  }
}
async function select(runId) {
  stopReplay();
  state.replay = null;
  state.selected = runId;
  state.events = [];
  state.details = null;
  state.patchText = "";
  state.patchRun = null;
  $("#replay-controls").classList.add("hidden");
  navigate("runs");
  await poll();
  await history();
  $("#run-section").scrollIntoView({ behavior: "smooth", block: "start" });
}
async function poll() {
  if (!state.selected || state.replay) return;
  const id = state.selected;
  try {
    const after = state.events.at(-1)?.sequence || 0;
    const [details, events] = await Promise.all([
      api(`/runs/${id}`),
      api(`/runs/${id}/events?after=${after}`),
    ]);
    if (state.selected !== id || state.replay) return;
    state.details = details;
    state.events.push(...events);
    if (events.some((e) => e.type === "patch.applied")) state.patchRun = null;
    renderRun();
    if (
      events.some((e) =>
        [
          "patch.applied",
          "run.completed",
          "run.stopped",
          "run.failed",
          "sandbox.closed",
        ].includes(e.type),
      )
    )
      history();
  } catch (e) {
    toast(e.message, true);
  }
}
async function launch(fixture = null) {
  if (state.busy) return;
  if (state.mode === "live" && !state.live) {
    openSettings();
    toast("Configure your model endpoints before starting Live AI.");
    return;
  }
  const url = $("#repo-url").value.trim();
  if (!fixture && !url) {
    $("#repo-url").focus();
    toast("Paste a GitHub URL or choose one of the broken examples.");
    return;
  }
  state.busy = true;
  $("#run-button").disabled = true;
  try {
    const result = await api("/runs", "POST", {
      fixture,
      repository_url: url,
      context_policy: $('input[name="context-policy"]:checked').value,
      user_goal: $("#user-goal").value.trim(),
      mode: state.mode,
      network: $("#network-access").checked,
      max_steps: $("#extended-repair").checked ? 100 : 25,
      max_seconds: $("#extended-repair").checked ? 1800 : 600,
    });
    $("#examples-panel").classList.add("hidden");
    await select(result.run_id);
    toast("Workspace queued. Watch the actual execution below.");
  } catch (e) {
    toast(e.message, true);
  } finally {
    state.busy = false;
    $("#run-button").disabled = false;
  }
}
function setMode(mode) {
  state.mode = mode;
  $$("[data-mode]").forEach((b) =>
    b.classList.toggle("selected", b.dataset.mode === mode),
  );
  $("#engine-description").innerHTML =
    mode === "live"
      ? '<span class="small-dot blue"></span> Supervisor + specialist models · real Docker execution · independent verification'
      : '<span class="small-dot blue"></span> Deterministic repair recipes · no API key needed · real Docker execution';
  if (mode === "live" && !state.live)
    toast(
      "Live AI needs model endpoints. Open Model settings to configure them.",
    );
}
function navigate(page) {
  if (page !== "replay" && state.details?.metadata.mode === "replay") {
    state.details = null;
    state.events = [];
    $("#replay-controls").classList.add("hidden");
    $("#active-run").classList.add("hidden");
    $("#empty-state").classList.remove("hidden");
    $("#run-title").textContent = "The work, in the open";
    $("#run-source").textContent = "LIVE OBSERVABILITY";
  }
  state.page = page;
  $$("[data-nav]").forEach((b) =>
    b.classList.toggle("active", b.dataset.nav === page),
  );
  $("#page-name").textContent = {
    runs: "Repository runs",
    benchmark: "Benchmarks",
    replay: "Replay a run",
  }[page];
  $("#benchmark-section").classList.toggle("hidden", page !== "benchmark");
  $("#replay-section").classList.toggle("hidden", page !== "replay");
  $("#launcher").classList.toggle("hidden", page !== "runs");
  $("#run-section").classList.toggle(
    "hidden",
    page !== "runs" && !state.replay,
  );
  $(".hero").classList.toggle("hidden", page !== "runs");
  $("#examples-panel").classList.add("hidden");
  if (page === "benchmark") loadBenchmarks();
}

function renderRun() {
  const details = state.details;
  if (!details) return;
  const r = details.run,
    m = details.metadata,
    events = state.events;
  const replay = !!state.replay;
  $("#empty-state").classList.add("hidden");
  $("#active-run").classList.remove("hidden");
  $("#run-title").textContent = runName(details);
  $("#run-source").textContent = replay
    ? "OFFLINE REPLAY"
    : m.mode === "live"
      ? "LIVE AI + REAL DOCKER"
      : "LOCAL RECIPES + REAL DOCKER";
  const running = r.status === "running",
    paused = r.status === "awaiting_approval";
  const status = replay
    ? "Offline replay"
    : r.status === "completed"
      ? "Verified. Rebuilt. Accepted."
      : paused
        ? r.verification?.checks?.every((c) => c.passed)
          ? "Ready for your review"
          : "A human decision is needed"
        : running
          ? "Working the problem."
          : r.status === "stopped"
            ? "Checkpoint saved. Review the blocker."
            : "Execution needs attention.";
  $("#run-summary").innerHTML =
    `<div class="verdict-icon ${running ? "running" : escape(r.grade)}">${running ? '<span class="spin">↻</span>' : r.grade === "GREEN" ? "✓" : paused ? "!" : "↗"}</div><div class="run-info"><strong>${escape(status)} <span class="run-badge">${escape(replay ? "REPLAY" : m.mode === "live" ? "LIVE AI" : "NO LLM")}</span></strong><p title="${escape(r.reason || m.repository_url || m.fixture)}">${escape(r.reason || m.repository_url || m.fixture)}</p></div><div class="run-metric"><strong>${escape(r.steps)}<span class="terminal-muted">/25</span></strong><small>ACTIONS</small></div><div class="run-metric"><strong>${escape(elapsed(details))}</strong><small>ELAPSED</small></div><div class="run-metric"><strong>${escape(r.rounds)}</strong><small>ROUNDS</small></div><div class="run-metric"><strong>${escape(running ? "—" : r.grade)}</strong><small>${m.approval_policy?.includes("automatic") ? "EVAL POLICY" : "VERDICT"}</small></div>`;
  $("#cancel-run").classList.toggle(
    "hidden",
    replay || !["running", "awaiting_approval"].includes(r.status),
  );
  $("#continue-run").classList.toggle("hidden", replay || !["stopped", "failed"].includes(r.status));
  const actionsMetric = $("#run-summary .run-metric strong span");
  if (actionsMetric) actionsMetric.textContent = `/${m.max_steps || 25}`;
  $("#export-replay").classList.toggle("hidden", replay);
  const bundle = details.artifacts.find((a) => a.name === "evidence.zip");
  const report = details.artifacts.find((a) => a.name === "REPORT.md");
  $("#download-report").classList.toggle("hidden", !report || replay);
  if (report) $("#download-report").href = report.url;
  $("#download-bundle").classList.toggle("hidden", !bundle || replay);
  if (bundle) $("#download-bundle").href = bundle.url;
  const approval = events.filter((e) => e.type === "approval.requested").at(-1);
  const approvalEl = $("#approval-panel");
  if (paused && !replay && approval) {
    const final =
      r.approval_kind === "final" ||
      (!r.approval_kind &&
        !!(
          r.verification?.checks?.length &&
          r.verification.checks.every((c) => c.passed)
        ));
    const review = r.approval_kind === "review";
    const existing = approvalEl.dataset.approval === r.pending_approval;
    if (!existing) {
      approvalEl.dataset.approval = r.pending_approval;
      approvalEl.innerHTML = `<div class="approval-top"><span class="approval-mark">${final ? "✓" : "!"}</span><div class="approval-copy"><h3>${final ? "The evidence is ready. The last call is yours." : review ? "A planning decision needs your review." : "A resource is missing. The agent is waiting."}</h3><p>${escape(approval.data.reason)}</p><div class="approval-controls">${final ? '<button class="primary-button approve" id="approve-final">Accept verified candidate ✓</button><button class="deny" id="review-evidence">Inspect evidence first ↗</button>' : review ? '<button class="primary-button approve" id="approve-final">Acknowledge review ↗</button>' : '<input id="resource-name" aria-label="Environment variable name" placeholder="Variable name, e.g. DEMO_API_KEY" autocomplete="off"><input id="resource-value" type="password" aria-label="Resource value" placeholder="Provisioned resource value" autocomplete="off"><button class="primary-button approve" id="provision-resource">Provision & resume ↗</button>'}<button class="deny" id="deny-approval">${final ? "Decline" : "Stop & report blocker"}</button></div></div></div>`;
    }
    approvalEl.classList.remove("hidden");
  } else {
    approvalEl.classList.add("hidden");
    delete approvalEl.dataset.approval;
  }
  const round =
    events.filter((e) => e.type === "round.started").at(-1)?.data.round || 1;
  $("#round-indicator").textContent = `ROUND ${String(round).padStart(2, "0")}`;
  const plan = events.filter((e) => e.type === "plan.created").at(-1);
  const last = events.at(-1);
  $("#supervisor-name").textContent = replay
    ? "Recorded supervisor"
    : m.mode === "live"
      ? m.supervisor_model || "Supervisory model"
      : "Local recipe supervisor";
  $("#supervisor-description").textContent = replay
    ? "Recorded decisions · no calls during replay"
    : m.mode === "live"
      ? "Decompose → delegate → reconcile"
      : "Deterministic planning · no LLM calls";
  $("#supervisor-state").textContent = paused
    ? "Paused"
    : running
      ? events.some((e) => e.type === "aggregation.accepted")
        ? "Observing"
        : "Planning"
      : r.status === "completed"
        ? "Accepted"
        : "Stopped";
  $("#supervisor-state").classList.toggle("active", running);
  if (plan) {
    const since = events.filter((e) => e.sequence > plan.sequence);
    $("#workers").innerHTML = plan.data.tasks
      .map((t) => {
        const done = since.find(
          (e) => e.type === "worker.completed" && e.data.task_id === t.task_id,
        );
        const started = since.find(
          (e) =>
            e.type === "worker.started" && e.data.task?.task_id === t.task_id,
        );
        return `<div class="worker"><div class="worker-top"><span class="worker-role">${t.specialist === "environment" ? "⌘" : t.specialist === "dependency" ? "⊞" : "⌁"}</span><strong>${escape(t.specialist.charAt(0).toUpperCase() + t.specialist.slice(1))}</strong><span class="worker-status ${started && !done ? "active" : ""}">${done ? "Complete" : started ? "Working" : "Queued"}</span></div><p title="${escape(t.objective)}">${escape(done ? Object.values(done.data.findings || {}).join(" · ") || "Structured findings returned" : t.objective.split("\n")[0])}</p><div class="worker-scope">${escape(t.scope.slice(0, 3).join(" · "))}${t.scope.length > 3 ? ` +${t.scope.length - 3}` : ""}</div></div>`;
      })
      .join("");
  } else {
    $("#workers").innerHTML =
      `<div class="worker-placeholder">${events.some((e) => e.type === "baseline.started") ? "Original tests run before specialists propose repairs. A passing baseline needs verification; a failing baseline is sent to the repair agents." : "Scoped specialists appear here when the plan is ready."}</div>`;
  }
  const stages = [
    ["Plan", events.some((e) => ["plan.created", "baseline.started"].includes(e.type))],
    ["Specialists", events.some((e) => e.type === "worker.completed")],
    ["Execute", events.some((e) => e.type === "execution.completed")],
    ["Verify", !!r.verification],
    [
      "Fresh build",
      r.verification?.checks?.some((c) => c.kind === "rebuild" && c.passed),
    ],
    ["Review", r.status === "completed"],
  ];
  $("#pipeline-status").innerHTML = stages
    .map(
      ([name, done]) =>
        `<span class="pipeline-step ${done ? "done" : ""}">${done ? "✓" : "○"} ${name}</span>`,
    )
    .join("");
  $("#log-count").textContent = events.filter(
    (e) => e.type === "execution.started",
  ).length;
  $("#patch-count").textContent = events.filter(
    (e) => e.type === "patch.applied",
  ).length;
  $("#evidence-count").textContent = details.artifacts.length;
  renderTab();
}

function selectTab(tab) {
  state.tab = tab;
  $$("[data-tab]").forEach((b) => {
    b.classList.toggle("selected", b.dataset.tab === tab);
    b.setAttribute("aria-selected", b.dataset.tab === tab ? "true" : "false");
  });
  renderTab();
}
function renderTab() {
  if (!state.details) return;
  const target = $("#tab-content"),
    events = state.events,
    details = state.details;
  if (state.tab === "logs") {
    const scroll = target.querySelector(".terminal-body");
    const stick =
      !scroll ||
      scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 45;
    const old = scroll?.scrollTop || 0;
    let text = "";
    for (const e of events) {
      const t = `<span class="terminal-time">${escape(time(e.timestamp))}</span> `;
      if (e.type === "execution.started")
        text += `<div class="log-separator"></div>${t}<span class="terminal-command">$ ${escape(e.data.argv.join(" "))}</span>\n`;
      if (e.type === "execution.output") {
        if (e.data.stdout.trim())
          text += `<span class="terminal-message">${escape(e.data.stdout.trimEnd())}</span>\n`;
        if (e.data.stderr.trim())
          text += `<span class="${e.data.exit_code ? "terminal-error" : "terminal-message"}">${escape(e.data.stderr.trimEnd())}</span>\n`;
      }
      if (e.type === "verification.completed" && e.data.result?.diagnostic)
        text += `${t}<span class="terminal-error">${escape(e.data.result.diagnostic.slice(-3500))}</span>\n`;
      if (
        [
          "patch.applied",
          "memory.hit",
          "rebuild.completed",
          "approval.requested",
          "run.completed",
          "run.stopped",
          "run.failed",
          "sandbox.ready",
          "verification.started",
          "sandbox.closed",
        ].includes(e.type)
      )
        text += `${t}<span class="${e.type === "run.completed" ? "terminal-success" : e.type === "run.failed" ? "terminal-error" : "terminal-muted"}">${escape(friendly(e.type))}${eventDetail(e) ? " · " + escape(eventDetail(e)) : ""}</span>\n`;
    }
    target.innerHTML = `<div class="terminal"><div class="terminal-bar"><i class="terminal-dot"></i><i class="terminal-dot"></i><i class="terminal-dot"></i><span>${state.replay ? "RECORDED EXECUTION · READ ONLY" : "SANDBOX OUTPUT · ACTUAL STDOUT / STDERR"}</span></div><div class="terminal-body">${text || '<span class="terminal-muted">Waiting for the first command. Repository code executes inside Docker.</span>\n'}${["running", "awaiting_approval"].includes(details.run.status) && !state.replay ? '<span class="cursor-blink"></span>' : ""}</div></div>`;
    const body = target.querySelector(".terminal-body");
    body.scrollTop = stick ? body.scrollHeight : old;
  } else if (state.tab === "timeline") {
    target.innerHTML = `<div class="timeline">${events
      .filter((e) => e.type !== "execution.output")
      .map(
        (e) =>
          `<div class="timeline-item"><span class="timeline-sequence">${String(e.sequence).padStart(3, "0")}</span><div><strong>${escape(friendly(e.type))}</strong>${eventDetail(e) ? `<p>${escape(eventDetail(e))}</p>` : ""}</div><time>${escape(time(e.timestamp))}</time></div>`,
      )
      .join("")}</div>`;
  } else if (state.tab === "diff") {
    const artifact = details.artifacts.find((a) => a.name === "changes.patch");
    if (state.replay) {
      target.innerHTML =
        '<div class="blank-panel">Replay contains the event trace. Inspect the original evidence bundle for full patches.</div>';
      return;
    }
    if (!artifact) {
      target.innerHTML =
        '<div class="blank-panel">No source patches applied yet. Original tests are protected from modification.</div>';
      return;
    }
    if (state.patchRun !== state.selected || state.patchText === null) {
      state.patchRun = state.selected;
      const id = state.selected;
      state.patchText = "";
      fetch(artifact.url)
        .then((r) => r.text())
        .then((text) => {
          if (state.selected === id) {
            state.patchText = text;
            if (state.tab === "diff") renderTab();
          }
        })
        .catch((e) => toast(e.message, true));
    }
    const lines = state.patchText
      .split("\n")
      .map(
        (line) =>
          `<span class="${line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@") ? "diff-header" : line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-remove" : ""}">${escape(line) || " "}</span>`,
      )
      .join("");
    target.innerHTML = `<div class="diff-view">${lines || "Loading the exact applied patch…"}</div>`;
  } else {
    const checks = details.run.verification?.checks || [],
      artifacts = details.artifacts;
    if (!artifacts.length) {
      target.innerHTML =
        '<div class="blank-panel">Evidence appears as the run executes. GREEN requires independent checks, a fresh rebuild, and approval.</div>';
      return;
    }
    target.innerHTML = `<div class="evidence-panel"><div class="checks-row">${checks.map((c) => `<span class="check ${c.passed ? "" : "failed"}">${c.passed ? "✓" : "×"} ${escape(c.name)}</span>`).join("")}</div><div class="artifacts">${artifacts.map((a) => `<a class="artifact" href="${escape(a.url)}" ${a.name.endsWith(".png") ? 'target="_blank" rel="noopener"' : ""}><span class="artifact-icon">${a.name.endsWith(".zip") ? "▣" : "≡"}</span><span><strong>${escape(a.name)}</strong><small>${a.bytes >= 1024 ? (a.bytes / 1024).toFixed(1) + " KB" : a.bytes + " bytes"}</small></span><span class="artifact-arrow">↓</span></a>`).join("")}</div>${artifacts.some((a) => a.name === "screenshot.png") ? `<img class="evidence-screenshot" src="${escape(artifacts.find((a) => a.name === "screenshot.png").url)}" alt="Captured sandbox HTTP response, rendered with scripts and external requests disabled" loading="lazy">` : ""}</div>`;
  }
}

async function approve(approved, refs = []) {
  if (!state.details?.run.pending_approval) return;
  const id = state.selected;
  try {
    await api(`/runs/${id}/approval`, "POST", {
      approval_id: state.details.run.pending_approval,
      approved,
      resource_refs: refs,
    });
    toast(
      approved
        ? "Decision received. The run is resuming."
        : "Run will stop and report the outcome.",
    );
    await poll();
  } catch (e) {
    toast(e.message, true);
  }
}
async function provision() {
  const name = $("#resource-name").value.trim(),
    value = $("#resource-value").value;
  if (!name || !value) {
    toast(
      "Provide a variable name and its provisioned value, or stop this run.",
    );
    return;
  }
  try {
    const result = await api(`/runs/${state.selected}/resources`, "POST", {
      name,
      value,
    });
    $("#resource-value").value = "";
    await approve(true, [result.resource_ref]);
  } catch (e) {
    toast(e.message, true);
  }
}

async function openSettings() {
  try {
    const s = await api("/settings");
    const form = $("#settings-form");
    for (const name of [
      "supervisor_url",
      "supervisor_model",
      "worker_url",
      "worker_model",
    ])
      form.elements[name].value = s[name] || "";
    for (const name of ["supervisor_key", "worker_key"]) {
      form.elements[name].value = "";
      form.elements[name].placeholder = s[name + "_configured"]
        ? "Configured key exists; re-enter it to retain it when saving"
        : "Optional for unauthenticated local endpoints";
    }
    $("#settings-dialog").showModal();
  } catch (e) {
    toast(e.message, true);
  }
}

async function loadBenchmarks() {
  try {
    const all = await api("/benchmarks");
    if (!all.length) return;
    const b = all[0],
      rows = b.results;
    const green = rows.filter((r) => r.grade === "GREEN").length,
      blocked = rows.filter(
        (r) => r.expected === "blocked" && r.status === "stopped",
      ).length;
    $("#start-benchmark").disabled = b.status === "running";
    $("#start-benchmark").textContent =
      b.status === "running"
        ? `Evaluating ${rows.length}/10 repositories…`
        : "Run 10-repository evaluation ↗";
    $("#benchmark-results").innerHTML =
      `<div class="benchmark-metrics"><div class="benchmark-metric"><strong>${green}/${rows.length || "–"}</strong><small>VERIFIED + REBUILT</small></div><div class="benchmark-metric"><strong>${blocked}/3</strong><small>BLOCKERS RECOGNIZED</small></div><div class="benchmark-metric"><strong>${Math.round(rows.reduce((n, r) => n + r.seconds, 0))}s</strong><small>TOTAL RECORDED TIME</small></div><div class="benchmark-metric"><strong>${rows.length}/10</strong><small>${escape(b.status.toUpperCase())}</small></div></div><div class="benchmark-table-wrap"><table class="benchmark-table"><thead><tr><th>REPOSITORY</th><th>EXPECTED</th><th>RESULT</th><th>ACTIONS</th><th>TIME</th><th>EVIDENCE</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${escape(r.title)}</td><td>${escape(r.expected)}</td><td><span class="grade ${escape(r.grade)}">${r.grade === "GREEN" ? "VERIFIED" : escape(r.status.toUpperCase())}</span></td><td>${r.steps}</td><td>${r.seconds}s</td><td><button data-run="${escape(r.run_id)}">Inspect run ↗</button></td></tr>`).join("")}</tbody></table></div>`;
  } catch (e) {
    toast(e.message, true);
  }
}

function stopReplay() {
  if (state.replayTimer) {
    clearInterval(state.replayTimer);
    state.replayTimer = null;
  }
  $("#replay-play").textContent = "▶ Play";
}
function validateReplay(events) {
  if (!events.length || events.length > 5000)
    throw new Error("Import a single run with 1–5000 events.");
  const strings = (value) =>
    Array.isArray(value) && value.every((x) => typeof x === "string");
  const task = (t) =>
    t &&
    typeof t.task_id === "string" &&
    typeof t.specialist === "string" &&
    typeof t.objective === "string" &&
    strings(t.scope);
  const sequences = new Set();
  const runId = events[0].run_id;
  for (const e of events) {
    if (
      !e ||
      !Number.isInteger(e.sequence) ||
      sequences.has(e.sequence) ||
      e.run_id !== runId ||
      typeof e.type !== "string" ||
      !e.data ||
      typeof e.data !== "object" ||
      Array.isArray(e.data) ||
      !Number.isFinite(Date.parse(e.timestamp))
    )
      throw new Error("Invalid or mixed-run timeline.");
    sequences.add(e.sequence);
    const d = e.data;
    for (const key of ["argv", "files", "issues"])
      if (d[key] !== undefined && !strings(d[key]))
        throw new Error("Invalid event detail in timeline.");
    if (d.task && !task(d.task))
      throw new Error("Invalid worker task in timeline.");
    if (
      e.type === "plan.created" &&
      (!Array.isArray(d.tasks) || d.tasks.length > 8 || !d.tasks.every(task))
    )
      throw new Error("Invalid plan in timeline.");
    if (
      e.type === "execution.output" &&
      (typeof d.stdout !== "string" || typeof d.stderr !== "string")
    )
      throw new Error("Invalid execution output in timeline.");
    if (e.type === "execution.started" && !strings(d.argv))
      throw new Error("Invalid command in timeline.");
    if (
      d.result &&
      (!Array.isArray(d.result.checks) ||
        !d.result.checks.every(
          (c) =>
            c &&
            typeof c.name === "string" &&
            typeof c.kind === "string" &&
            typeof c.passed === "boolean",
        ))
    )
      throw new Error("Invalid verification result in timeline.");
  }
  return events.sort((a, b) => a.sequence - b.sequence);
}
function replayAt(position) {
  if (!state.replay) return;
  const controls = $("#replay-controls");
  if (controls.parentElement.id !== "run-section")
    $("#run-section").insertBefore(controls, $("#active-run"));
  const events = state.replay.events.slice(0, position);
  state.events = events;
  const last = events.at(-1),
    verification = events
      .filter((e) => e.type === "verification.completed")
      .at(-1)?.data.result;
  const terminal = events
    .filter((e) =>
      ["run.completed", "run.failed", "run.stopped"].includes(e.type),
    )
    .at(-1);
  const started = state.replay.events[0]?.timestamp;
  state.details = {
    run: {
      run_id: state.replay.runId,
      status: terminal ? terminal.type.slice(4) : "running",
      grade: terminal?.data.grade || "RED",
      reason: "Read-only replay of recorded events",
      steps:
        events.filter((e) => e.type === "budget.step").at(-1)?.data.steps || 0,
      rounds:
        events.filter((e) => e.type === "round.started").at(-1)?.data.round ||
        0,
      pending_approval: null,
      verification,
    },
    metadata: {
      repository_url: "Imported execution timeline",
      mode: "replay",
      started_at: started,
      finished_at: last?.timestamp || started,
      elapsed_seconds: last
        ? (Date.parse(last.timestamp) - Date.parse(started)) / 1000
        : 0,
      fixture: null,
      approval_policy: "recorded",
    },
    artifacts: [],
  };
  $("#replay-slider").value = position;
  $("#replay-position").textContent =
    `${position} / ${state.replay.events.length}`;
  $("#run-section").classList.remove("hidden");
  renderRun();
  if (position >= state.replay.events.length) stopReplay();
}
function playReplay() {
  if (!state.replay) return;
  if (state.replayTimer) {
    stopReplay();
    return;
  }
  let position = Number($("#replay-slider").value);
  if (position >= state.replay.events.length) position = 0;
  $("#replay-play").textContent = "Ⅱ Pause";
  state.replayTimer = setInterval(
    () => replayAt(++position),
    Number($("#replay-speed").value),
  );
}

document.addEventListener("click", async (e) => {
  const run = e.target.closest("[data-run]");
  if (run) {
    select(run.dataset.run);
    return;
  }
  const fixture = e.target.closest("[data-fixture]");
  if (fixture) {
    launch(fixture.dataset.fixture);
    return;
  }
  const nav = e.target.closest("[data-nav]");
  if (nav) {
    if (nav.dataset.nav !== "replay") {
      stopReplay();
      state.replay = null;
    }
    navigate(nav.dataset.nav);
    return;
  }
  const mode = e.target.closest("[data-mode]");
  if (mode) {
    setMode(mode.dataset.mode);
    return;
  }
  const tab = e.target.closest("[data-tab]");
  if (tab) {
    selectTab(tab.dataset.tab);
    return;
  }
  const id = e.target.closest("button")?.id;
  if (id === "settings-button" || id === "top-model-settings") openSettings();
  if (id === "close-settings") {
    $("#settings-form").reset();
    $("#settings-dialog").close();
  }
  if (id === "refresh-history") history();
  if (id === "continue-run" && state.selected) {
    if (state.busy) return;
    state.busy = true;
    try {
      const result = await api("/runs", "POST", {resume_from: state.selected, mode: state.details?.metadata.mode || state.mode,
        context_policy: $('input[name="context-policy"]:checked').value,
        user_goal: $("#user-goal").value.trim(),
        network: $("#network-access").checked, max_steps: $("#extended-repair").checked ? 100 : 25,
        max_seconds: $("#extended-repair").checked ? 1800 : 600});
      await select(result.run_id);
      toast("Continuing the saved candidate with the previous failure context.");
    } catch (err) { toast(err.message, true); }
    finally { state.busy = false; }
  }
  if (id === "all-examples") $("#examples-panel").classList.toggle("hidden");
  if (id === "close-examples") $("#examples-panel").classList.add("hidden");
  if (id === "approve-final") approve(true);
  if (id === "deny-approval") approve(false);
  if (id === "provision-resource") provision();
  if (id === "review-evidence") {
    selectTab("evidence");
    $("#tab-content").scrollIntoView({ behavior: "smooth" });
  }
  if (id === "export-replay" && state.selected)
    window.location.href = `/api/runs/${state.selected}/replay`;
  if (id === "cancel-run" && state.selected) {
    try {
      await api(`/runs/${state.selected}`, "DELETE");
      toast("Stopping the run and cleaning up its sandbox.");
    } catch (err) {
      toast(err.message, true);
    }
  }
  if (id === "start-benchmark") {
    try {
      await api("/benchmarks", "POST");
      $("#start-benchmark").disabled = true;
      $("#start-benchmark").textContent = "Starting real Docker evaluation…";
      toast(
        "Evaluation started. Results are recorded as each repository finishes.",
      );
      await loadBenchmarks();
    } catch (err) {
      toast(err.message, true);
    }
  }
  if (id === "replay-play") playReplay();
});
$("#launch-form").addEventListener("submit", (e) => {
  e.preventDefault();
  launch();
});
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("[type=submit]");
  button.disabled = true;
  try {
    await api("/settings", "PUT", Object.fromEntries(new FormData(e.target)));
    e.target.reset();
    $("#settings-dialog").close();
    await health();
    toast(
      "Model configuration saved in server memory. Start a Live AI run to use it.",
    );
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
});
$("#settings-dialog").addEventListener("cancel", () =>
  $("#settings-form").reset(),
);
$("#replay-slider").addEventListener("input", (e) => {
  stopReplay();
  replayAt(Number(e.target.value));
});
$("#replay-speed").addEventListener("change", () => {
  if (state.replayTimer) {
    stopReplay();
    playReplay();
  }
});
$("#replay-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size > 10_000_000) throw new Error("Timeline file exceeds 10 MB.");
    const text = await file.text();
    const events = validateReplay(
      text
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((line) => JSON.parse(line)),
    );
    const runId = events[0].run_id;
    stopReplay();
    state.replay = { events, runId };
    state.selected = null;
    $("#replay-controls").classList.remove("hidden");
    $("#replay-slider").max = events.length;
    replayAt(0);
    $("#run-section").scrollIntoView({ behavior: "smooth", block: "start" });
    toast("Timeline loaded. Press Play to replay without executing anything.");
  } catch (err) {
    toast(err.message, true);
  }
  e.target.value = "";
});
document.addEventListener("keydown", (e) => {
  if (
    (e.metaKey || e.ctrlKey) &&
    e.key === "Enter" &&
    !$("#settings-dialog").open
  ) {
    e.preventDefault();
    launch();
  }
});

async function init() {
  try {
    state.fixtures = await api("/fixtures");
    const shortcuts = ["python-dependency", "python-web", "python-secret"];
    const labels = {
      "python-dependency": "Missing dependency",
      "python-web": "Broken web app",
      "python-secret": "Missing secret",
    };
    $("#fixture-shortcuts").innerHTML = shortcuts
      .map(
        (id) =>
          `<button class="fixture-chip" data-fixture="${id}"><span>↗</span>${labels[id]}</button>`,
      )
      .join("");
    $("#examples-grid").innerHTML = state.fixtures
      .map(
        (f) =>
          `<button class="example-card" data-fixture="${escape(f.id)}"><span>${escape(f.stack.toUpperCase())} / ${escape(f.expected.toUpperCase())}</span><strong>${escape(f.title)} ↗</strong><p>${escape(f.description)}</p></button>`,
      )
      .join("");
    await Promise.all([health(), history()]);
    if (state.runs.length && state.page === "runs" && !state.selected)
      await select(state.runs[0].run.run_id);
  } catch (e) {
    toast(e.message, true);
  }
}
init();
setInterval(() => {
  if (!document.hidden) {
    poll();
    if (state.page === "benchmark") loadBenchmarks();
  }
}, 1200);
setInterval(() => {
  if (!document.hidden) {
    health();
    history();
  }
}, 15000);
