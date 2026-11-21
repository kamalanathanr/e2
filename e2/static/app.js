/* E2 dashboard: overview strip, project tabs, vertical stage panes, full-screen stage detail. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { meta: null, overview: null, pid: null, data: null, rid: null, stage: null, logOff: 0, logRid: null };
const ICON = { success: "✓", failed: "✕", running: "●", skipped: "–", cancelled: "■", interrupted: "!", pending: "", queued: "…" };

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), 4000);
}
const ago = (iso) => { if (!iso) return ""; const s = (Date.now() - new Date(iso)) / 1000; return s < 60 ? `${s | 0}s ago` : s < 3600 ? `${(s / 60) | 0}m ago` : s < 86400 ? `${(s / 3600) | 0}h ago` : `${(s / 86400) | 0}d ago`; };
const dur = (a, b) => { if (!a) return ""; const s = ((b ? new Date(b) : Date.now()) - new Date(a)) / 1000; return s < 60 ? `${s.toFixed(0)}s` : `${(s / 60) | 0}m ${(s % 60) | 0}s`; };
const badge = (st) => `<span class="badge s-${esc(st)}">${esc(st)}</span>`;

/* ── stage option schemas (what each stage can be configured to do) ── */
const SCHEMA = {
  source: [
    { k: "type", label: "Source", type: "select", options: [["git", "Git repository (clone / pull)"], ["local", "Local folder (copy)"]] },
    { k: "url", label: "Repository URL", type: "text", ph: "https://github.com/org/repo.git or git@…", show: (c) => c.type === "git", hint: "Uses your machine's git credentials (SSH keys / credential helper)." },
    { k: "branch", label: "Branch", type: "branch", show: (c) => c.type === "git" },
    { k: "commit", label: "Pin to commit or tag (optional)", type: "text", ph: "leave empty for branch tip", show: (c) => c.type === "git" },
    { k: "local_path", label: "Local folder", type: "text", ph: "/Users/me/projects/app", show: (c) => c.type === "local", hint: "Copied into the workspace; node_modules, target, build, .venv etc. are skipped." },
    { k: "trigger", label: "Trigger", type: "select", options: [["manual", "Manual: run when I click Run"], ["auto", "Automatic: run when new code appears"]] },
    { k: "poll_seconds", label: "Check for changes every (s)", type: "number", show: (c) => c.trigger === "auto" },
  ],
  build: [
    { k: "technology", label: "Technology", type: "select", options: [["auto", "Auto-detect from source"], ["maven", "Maven"], ["gradle", "Gradle"], ["npm", "Node.js (npm / yarn / pnpm)"], ["python", "Python (pip / uv)"], ["go", "Go"], ["dotnet", ".NET"], ["rust", "Rust (cargo)"], ["flutter", "Flutter"], ["xcode", "Xcode"], ["custom", "Custom command"]] },
    { k: "command", label: "Custom build command (optional)", type: "text", ph: "e.g. mvn -B package" },
  ],
  platform: [
    { group: "Desktop" },
    { k: "mac", label: "macOS", type: "check", sub: ".app / .dmg / .pkg" },
    { k: "windows", label: "Windows", type: "check", sub: ".exe / .msi" },
    { k: "linux", label: "Linux", type: "check", sub: "binary / .deb / AppImage" },
    { group: "Mobile" },
    { k: "ios", label: "iOS", type: "check", sub: ".ipa → TestFlight / App Store" },
    { k: "android", label: "Android", type: "check", sub: ".apk / .aab → Play Store" },
  ],
  test: [
    { k: "unit", label: "Unit tests", type: "check", sub: "Fast, run on every build" },
    { k: "integration", label: "Integration tests", type: "check", sub: "Components together" },
    { k: "regression", label: "Regression suite", type: "check", sub: "Full suite against previous behaviour" },
    { k: "docker", label: "Run tests in Docker", type: "check", sub: "Clean, reproducible container" },
    { k: "security", label: "Security review", type: "check", sub: "Dependency CVEs, secrets, static analysis" },
    { k: "license", label: "License / patent / copied-code review", type: "check", sub: "Third-party and referenced code provenance" },
  ],
  verify: [
    { k: "checksum", label: "Checksum (SHA-256)", type: "check", sub: "Record and verify artifact hashes" },
    { k: "signature", label: "Code signing check", type: "check", sub: "Signed / notarised binaries" },
    { k: "smoke", label: "Smoke test the artifact", type: "check", sub: "Launch it and check it starts" },
  ],
  publish: [
    { k: "destination", label: "Destination", type: "select", options: [["local", "Local folder"], ["artifactory", "Artifactory / Nexus"], ["github", "GitHub Releases"], ["docker", "Docker registry"], ["appstore", "App Store Connect / TestFlight (iOS)"], ["play", "Google Play (Android)"], ["msstore", "Microsoft Store (Windows)"]] },
    { k: "target", label: "Destination path / URL", type: "text", ph: "/path/to/releases or https://…" },
  ],
};
const optsLine = (key, c) => {
  if (!c) return "";
  if (key === "source") return c.type === "git" ? `${c.url || "no repo set"} · ${c.branch || "?"}${c.commit ? " @ " + c.commit : ""} · ${c.trigger}` : `${c.local_path || "no folder set"} · ${c.trigger}`;
  if (key === "build") return c.technology === "auto" ? "auto-detect" : c.technology + (c.command ? ` · ${c.command}` : "");
  if (key === "publish") return `${c.destination}${c.target ? " → " + c.target : ""}`;
  const labels = SCHEMA[key].filter((f) => f.type === "check" && c[f.k]).map((f) => f.label);
  return labels.join(" · ") || "nothing selected";
};

function formHTML(key, cfg, prefix = "f") {
  let h = `<div class="${key === "source" || key === "build" || key === "publish" ? "form-grid" : "checks"}">`;
  for (const f of SCHEMA[key]) {
    if (f.group) { h += `<div class="group-h">${esc(f.group)}</div>`; continue; }
    if (f.show && !f.show(cfg)) continue;
    const id = `${prefix}-${key}-${f.k}`, v = cfg[f.k];
    if (f.type === "check") { h += `<label class="check"><input type="checkbox" id="${id}" data-k="${f.k}" ${v ? "checked" : ""}><span>${esc(f.label)}<small>${esc(f.sub || "")}</small></span></label>`; continue; }
    h += `<div><label class="form-label" for="${id}">${esc(f.label)}</label>`;
    if (f.type === "select") h += `<select class="form-input" id="${id}" data-k="${f.k}">${f.options.map(([o, l]) => `<option value="${o}" ${o === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    else if (f.type === "branch") h += `<div class="row" style="flex-wrap:nowrap"><input class="form-input" id="${id}" data-k="${f.k}" list="${id}-dl" value="${esc(v)}"><datalist id="${id}-dl"></datalist><button type="button" class="btn ghost sm" data-branches="${id}">Load</button></div>`;
    else h += `<input class="form-input" id="${id}" data-k="${f.k}" type="${f.type === "number" ? "number" : "text"}" value="${esc(v)}" placeholder="${esc(f.ph || "")}">`;
    if (f.hint) h += `<div class="hint">${esc(f.hint)}</div>`;
    h += `</div>`;
  }
  return h + "</div>";
}
function readForm(root) {
  const o = {};
  root.querySelectorAll("[data-k]").forEach((el) => { o[el.dataset.k] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value.trim(); });
  return o;
}
function wireForm(root, key, cfg, rerender) {
  root.querySelectorAll("select[data-k]").forEach((el) => el.addEventListener("change", () => rerender({ ...cfg, ...readForm(root) })));
  root.querySelectorAll("[data-branches]").forEach((b) => b.addEventListener("click", async () => {
    const url = readForm(root).url;
    if (!url) return toast("Enter the repository URL first", true);
    b.disabled = true; b.textContent = "…";
    try {
      const { branches } = await api("/branches?url=" + encodeURIComponent(url));
      $("#" + b.dataset.branches + "-dl").innerHTML = branches.map((x) => `<option value="${esc(x)}">`).join("");
      toast(`${branches.length} branches found. Pick one from the Branch field`);
    } catch (e) { toast(e.message, true); }
    b.disabled = false; b.textContent = "Load";
  }));
}

/* ── overview strip + tabs ── */
async function loadOverview() {
  S.overview = await api("/overview");
  const c = S.overview.counts;
  $("#stats").innerHTML = [["Projects", c.projects, ""], ["Running", c.running, "run"], ["Succeeded", c.success, "ok"], ["Failed", c.failed, "crit"], ["Needs attention", c.attention, "warn"], ["Auto-trigger", c.auto, ""]]
    .map(([k, v, cl]) => `<div class="stat ${v ? cl : ""}"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
  const ps = S.overview.projects;
  if (!S.pid || !ps.find((p) => p.id === S.pid)) S.pid = (ps.find((p) => p.id === localStorage.getItem("e2-pid")) || ps[0] || {}).id || null;
  $("#tabs").innerHTML = ps.map((p) => `<button class="tab ${p.id === S.pid ? "active" : ""} s-${esc(p.last_run?.status || "none")}" data-pid="${esc(p.id)}"><span class="sdot"></span>${esc(p.name)}</button>`).join("")
    + `<button class="tab" id="new-btn">＋ New project</button><span class="spacer"></span>`
    + (ps.length > 1 ? `<select class="form-input inline" id="proj-sel">${ps.map((p) => `<option value="${esc(p.id)}" ${p.id === S.pid ? "selected" : ""}>${esc(p.name)} · v${esc(p.version)}</option>`).join("")}</select>` : "");
  $("#tabs").querySelectorAll("[data-pid]").forEach((b) => b.onclick = () => selectProject(b.dataset.pid));
  $("#new-btn").onclick = newProject;
  if ($("#proj-sel")) $("#proj-sel").onchange = (e) => selectProject(e.target.value);
}
function selectProject(pid) {
  S.pid = pid; S.rid = null; try { localStorage.setItem("e2-pid", pid); } catch (e) {}
  refresh();
}

/* ── project view ── */
async function loadProject() {
  if (!S.pid) { $("#main").innerHTML = `<div class="card"><div class="empty"><p>No projects yet.</p><button class="btn" onclick="newProject()">＋ Create your first project</button></div></div>`; return; }
  S.data = await api(`/projects/${S.pid}`);
  if (!S.rid || !S.data.runs.find((r) => r.id === S.rid)) S.rid = S.data.runs[0]?.id || null;
  renderProject();
  if (S.stage) renderStage();
}
const curRun = () => S.data?.runs.find((r) => r.id === S.rid) || null;

function renderProject() {
  const { project: p, runs, active } = S.data, r = curRun();
  const stageOf = (k) => r ? r.stages[k] : { status: "pending" };
  const resumable = r && ["failed", "cancelled", "interrupted"].includes(r.status);
  $("#main").innerHTML = `
  <div class="proj-head">
    <div>
      <h2>${esc(p.name)} <span class="ver">v${esc(p.version)}</span></h2>
      <div class="muted">${esc(optsLine("source", p.config.source))}</div>
    </div>
    <div class="row">
      ${runs.length ? `<select class="form-input inline" id="run-sel" title="Choose run">${runs.map((x) => `<option value="${x.id}" ${x.id === S.rid ? "selected" : ""}>Run #${x.number} · v${esc(x.version)} · ${esc(x.status)}</option>`).join("")}</select>` : ""}
      ${r && r.status === "running" ? `<button class="btn danger" id="cancel-btn">■ Cancel</button>` : ""}
      ${resumable && !active ? `<button class="btn warn" id="resume-btn">↻ Resume #${r.number}</button>` : ""}
      <select class="form-input inline" id="bump">
        <option value="patch">Patch → ${esc(nextV(p.version, "patch"))}</option>
        <option value="minor">Minor → ${esc(nextV(p.version, "minor"))}</option>
        <option value="major">Major → ${esc(nextV(p.version, "major"))}</option>
        <option value="none">Rebuild ${esc(p.version)}</option>
        <option value="custom">Custom version…</option>
      </select>
      <button class="btn" id="run-btn" ${active ? "disabled" : ""}>▶ Run pipeline</button>
    </div>
  </div>
  <div class="pipeline">${S.meta.stages.map((s, i) => { const st = stageOf(s.key).status; return `${i ? `<span class="link ${st === "success" || st === "skipped" ? "done" : ""}"></span>` : ""}<span class="chip s-${st}" data-open="${s.key}"><span class="sdot s-${st}"></span>${esc(s.label)}</span>`; }).join("")}</div>
  ${r ? `<div class="muted" style="margin:-8px 0 12px;font-size:12px">Run #${r.number} · ${esc(r.trigger)} · v${esc(r.version)} · ${badge(r.status)} · started ${esc(ago(r.started || r.created))}${r.started ? " · " + dur(r.started, r.finished) : ""}</div>` : ""}
  <div class="stages">${S.meta.stages.map((s, i) => {
    const st = stageOf(s.key);
    return `<div class="stage s-${st.status}" data-open="${s.key}">
      <div class="rail"></div><div class="num">${ICON[st.status] || i + 1}</div>
      <div class="body">
        <div class="title">${i + 1}. ${esc(s.label)} ${badge(st.status)} ${s.implemented ? "" : `<span class="badge soon">coming next</span>`}</div>
        <div class="sum">${esc(st.summary || (r ? "" : "Not run yet"))}</div>
        <div class="opts">${esc(optsLine(s.key, p.config[s.key]))}</div>
      </div>
      <div class="side">${st.started ? esc(dur(st.started, st.finished)) : ""}<br>${st.finished ? esc(ago(st.finished)) : ""}</div>
    </div>`; }).join("")}</div>
  <div class="card" style="margin-top:16px"><div class="card-h"><h3>Versions &amp; runs</h3><span class="muted" style="font-size:12px">Click a row to inspect that run</span></div>
    ${runs.length ? `<div style="overflow-x:auto"><table><tr><th>Run</th><th>Version</th><th>Status</th><th>Trigger</th><th>Commit</th><th>Started</th><th>Duration</th></tr>
    ${runs.map((x) => `<tr class="clk ${x.id === S.rid ? "sel" : ""}" data-rid="${x.id}"><td class="mono">#${x.number}</td><td class="mono">v${esc(x.version)}</td><td>${badge(x.status)}</td><td>${esc(x.trigger)}</td><td class="mono">${esc((x.stages.source.detail?.commit || "").slice(0, 8))}</td><td>${esc(ago(x.created))}</td><td class="mono">${esc(dur(x.started, x.finished))}</td></tr>`).join("")}</table></div>` : `<div class="empty">No runs yet. Click <b>Run pipeline</b>.</div>`}
  </div>
  <div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn ghost sm" id="del-btn">Delete project</button></div>`;

  $("#main").querySelectorAll("[data-open]").forEach((el) => el.onclick = () => { S.stage = el.dataset.open; S.logOff = 0; S.logRid = null; renderStage(); });
  $("#main").querySelectorAll("[data-rid]").forEach((el) => el.onclick = () => { S.rid = el.dataset.rid; renderProject(); });
  if ($("#run-sel")) $("#run-sel").onchange = (e) => { S.rid = e.target.value; renderProject(); };
  $("#run-btn").onclick = startRun;
  if ($("#cancel-btn")) $("#cancel-btn").onclick = () => act(`/projects/${S.pid}/runs/${S.rid}/cancel`, "Cancelling…");
  if ($("#resume-btn")) $("#resume-btn").onclick = () => act(`/projects/${S.pid}/runs/${S.rid}/resume`, "Resuming from the first unfinished stage");
  $("#del-btn").onclick = async () => {
    if (!confirm(`Delete project "${p.name}" with all its runs, logs and workspace?`)) return;
    try { await api(`/projects/${S.pid}`, { method: "DELETE" }); S.pid = null; refresh(); } catch (e) { toast(e.message, true); }
  };
}
function nextV(v, b) { const [a, m, p] = v.split(".").map(Number); return b === "major" ? `${a + 1}.0.0` : b === "minor" ? `${a}.${m + 1}.0` : `${a}.${m}.${p + 1}`; }
async function startRun() {
  const bump = $("#bump").value, body = { bump };
  if (bump === "custom") { const v = prompt("Version to build (MAJOR.MINOR.PATCH):", S.data.project.version); if (!v) return; body.version = v.trim(); }
  try { const r = await api(`/projects/${S.pid}/runs`, { method: "POST", body }); S.rid = r.id; toast(`Run #${r.number} started · v${r.version}`); refresh(); } catch (e) { toast(e.message, true); }
}
async function act(path, msg) { try { await api(path, { method: "POST" }); toast(msg); refresh(); } catch (e) { toast(e.message, true); } }

/* ── full-screen stage detail ── */
function renderStage() {
  const key = S.stage, meta = S.meta.stages.find((s) => s.key === key), r = curRun(), st = r?.stages[key];
  const ov = $("#overlay"), cfg = S.data.project.config[key];
  const keepForm = ov.dataset.stage === key && ov.dataset.rid === String(S.rid) && !ov.classList.contains("hidden");
  const det = st?.detail || {};
  const info = `<dl class="kv">
      <dt>Status</dt><dd>${st ? badge(st.status) : "–"} ${meta.implemented ? "" : `<span class="badge soon">coming next</span>`}</dd>
      ${r ? `<dt>Run</dt><dd>#${r.number} · v${esc(r.version)} · ${esc(r.trigger)}</dd>` : ""}
      ${st?.started ? `<dt>Duration</dt><dd>${esc(dur(st.started, st.finished))}</dd>` : ""}
      ${st?.summary ? `<dt>Summary</dt><dd>${esc(st.summary)}</dd>` : ""}
      ${Object.entries(det).filter(([k, v]) => v !== null && v !== "" && k !== "fingerprint").map(([k, v]) => `<dt>${esc(k)}</dt><dd class="${["commit", "path", "url"].includes(k) ? "mono" : ""}">${esc(Array.isArray(v) ? v.join(", ") : v)}</dd>`).join("")}
    </dl>`;
  if (keepForm) { $("#stage-info").innerHTML = info; $("#ov-status").innerHTML = st ? badge(st.status) : ""; pollLog(); return; }
  ov.dataset.stage = key; ov.dataset.rid = S.rid;
  ov.className = "overlay";
  ov.innerHTML = `
    <div class="ov-h"><button class="btn ghost sm" id="ov-close">← Back</button><h2>${S.meta.stages.indexOf(meta) + 1}. ${esc(meta.label)}</h2><span id="ov-status">${st ? badge(st.status) : ""}</span><span class="spacer" style="flex:1"></span>
      ${S.meta.stages.map((s) => `<button class="btn sm ${s.key === key ? "" : "ghost"}" data-go="${s.key}">${esc(s.label)}</button>`).join("")}</div>
    <div class="ov-b">
      <div class="card"><div class="card-h"><h3>Details &amp; options</h3></div><div class="card-b">
        <div id="stage-info">${info}</div>
        ${meta.implemented ? "" : `<p class="hint" style="margin:0 0 10px">This stage is planned. The options you choose are saved now and will be used once the stage is built.</p>`}
        <div id="stage-form"></div>
        <div class="row" style="margin-top:14px"><button class="btn" id="save-cfg">Save options</button><span class="hint">Applies to the next run.</span></div>
      </div></div>
      <div class="card"><div class="card-h"><h3>Log${r ? ` · run #${r.number}` : ""}</h3><label class="row muted" style="font-size:12px"><input type="checkbox" id="follow" checked> follow</label></div><pre class="log" id="log">${r ? "" : "No run selected."}</pre></div>
    </div>`;
  const draw = (c) => { $("#stage-form").innerHTML = formHTML(key, c, "o"); wireForm($("#stage-form"), key, c, draw); };
  draw(cfg);
  $("#ov-close").onclick = closeStage;
  ov.querySelectorAll("[data-go]").forEach((b) => b.onclick = () => { S.stage = b.dataset.go; S.logOff = 0; S.logRid = null; ov.dataset.stage = ""; renderStage(); });
  $("#save-cfg").onclick = async () => {
    try { await api(`/projects/${S.pid}`, { method: "PUT", body: { config: { [key]: readForm($("#stage-form")) } } }); toast("Options saved"); await loadProject(); } catch (e) { toast(e.message, true); }
  };
  S.logOff = 0; S.logRid = S.rid; pollLog();
}
function closeStage() { S.stage = null; const ov = $("#overlay"); ov.className = "overlay hidden"; ov.innerHTML = ""; ov.dataset.stage = ""; }
async function pollLog() {
  if (!S.stage || !S.rid || pollLog.busy) return;
  pollLog.busy = true;
  try {
    const key = S.stage, rid = S.rid;
    const j = await api(`/projects/${S.pid}/runs/${rid}/logs/${key}?offset=${S.logOff}`);
    if (S.stage !== key || S.rid !== rid) return;
    const el = $("#log");
    if (!el) return;
    if (S.logOff === 0) el.textContent = "";
    el.textContent += j.text;
    if (!el.textContent) el.textContent = "(no log output for this stage yet)";
    else if (el.textContent.startsWith("(no log")) el.textContent = j.text;
    S.logOff = j.offset;
    if ($("#follow")?.checked) el.scrollTop = el.scrollHeight;
  } catch (e) { /* run deleted or server restarting */ } finally { pollLog.busy = false; }
}

/* ── new project ── */
function newProject() {
  const ov = $("#overlay"); ov.dataset.stage = "";
  let cfg = { ...S.meta.defaults.source };
  ov.className = "overlay dim";
  ov.innerHTML = `<div class="card modal"><div class="card-h"><h3>New project</h3><button class="btn ghost sm" id="np-x">✕</button></div><div class="card-b">
    <div class="form-grid"><div><label class="form-label">Project name</label><input class="form-input" id="np-name" placeholder="My App"></div>
    <div><label class="form-label">Starting version</label><input class="form-input mono" id="np-ver" value="0.1.0"><div class="hint">MAJOR.MINOR.PATCH. Each run bumps it as you choose.</div></div></div>
    <div class="group-h">Stage 1 · Source</div><div id="np-form"></div>
    <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn ghost" id="np-cancel">Cancel</button><button class="btn" id="np-ok">Create project</button></div>
  </div></div>`;
  const draw = (c) => { cfg = c; $("#np-form").innerHTML = formHTML("source", c, "n"); wireForm($("#np-form"), "source", c, draw); };
  draw(cfg);
  const close = () => { ov.className = "overlay hidden"; ov.innerHTML = ""; };
  $("#np-x").onclick = $("#np-cancel").onclick = close;
  $("#np-ok").onclick = async () => {
    try {
      const p = await api("/projects", { method: "POST", body: { name: $("#np-name").value, version: $("#np-ver").value, config: { source: { ...cfg, ...readForm($("#np-form")) } } } });
      close(); toast(`Project "${p.name}" created`); selectProject(p.id);
    } catch (e) { toast(e.message, true); }
  };
  $("#np-name").focus();
}

/* ── refresh loop: faster while something is running ── */
async function refresh() {
  try { await loadOverview(); await loadProject(); } catch (e) { toast(e.message, true); }
}
function tick() {
  const busy = S.overview?.counts.running > 0;
  refresh().finally(() => setTimeout(tick, busy ? 1500 : 5000));
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.stage) closeStage(); });
$("#theme-btn").onclick = () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; try { localStorage.setItem("e2-theme", t); } catch (e) {} };
(async () => { S.meta = await api("/meta"); tick(); setInterval(pollLog, 1000); })();
