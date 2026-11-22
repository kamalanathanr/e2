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
    { group: "Installable app (Python projects)" },
    { k: "installer", label: "Build an installable app", type: "check", sub: "Bundles Python + all dependencies. macOS: .app in a .dmg (built here). Windows: .exe in a .zip (built on GitHub Actions)" },
    { k: "app_name", in: "installer", label: "App name", type: "text", ph: "defaults to the project name" },
    { k: "app_url", in: "installer", label: "Open in browser after start (optional)", type: "text", ph: "e.g. http://localhost:8000", hint: "For web-server apps: opens this page once the app has started." },
    { k: "app_env", in: "installer", label: "App settings (optional)", type: "text", ph: "MYAPP_DATA_DIR={appdata}/data; MYAPP_MODE=desktop", hint: "KEY=value pairs separated by ';'. {appdata} is the app's own data folder (~/Library/Application Support/<App> or %APPDATA%\\<App>)." },
    { group: "Release documents" },
    { k: "readme", label: "Include README", type: "check", sub: "Copied from the source into the release" },
    { k: "readme_path", in: "readme", label: "README file", type: "text", ph: "README.md", hint: "Path relative to the repository root. The build fails if it is missing." },
    { k: "icon", label: "App icon", type: "check", sub: "Used as the macOS / Windows app icon and shipped with the release" },
    { k: "icon_path", in: "icon", label: "Icon file", type: "text", ph: "icon.png", hint: "Path relative to the repository root. Must be one square 1024 × 1024 PNG; the build stops with an error otherwise. E2 converts it for each target: .icns for macOS, .ico for Windows." },
    { k: "release_notes", label: "Include release notes", type: "check", sub: "From the source; generated from the commits if the file is not there" },
    { k: "release_notes_path", in: "release_notes", label: "Release notes file", type: "text", ph: "RELEASE_NOTES.md", hint: "Path relative to the repository root." },
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
    if (f.in || (f.show && !f.show(cfg))) continue;  // attached fields are drawn inside their checkbox's box
    const id = `${prefix}-${key}-${f.k}`, v = cfg[f.k];
    const kids = SCHEMA[key].filter((x) => x.in === f.k);
    if (f.type === "check" && kids.length) {
      h += `<div class="check-box ${v ? "on" : ""}"><label class="check"><input type="checkbox" id="${id}" data-k="${f.k}" ${v ? "checked" : ""}><span>${esc(f.label)}<small>${esc(f.sub || "")}</small></span></label>`;
      kids.forEach((x) => {
        const xid = `${prefix}-${key}-${x.k}`;
        h += `<div class="check-sub" ${v ? "" : "hidden"}><label class="form-label" for="${xid}">${esc(x.label)}</label><input class="form-input mono" id="${xid}" data-k="${x.k}" value="${esc(cfg[x.k] ?? "")}" placeholder="${esc(x.ph || "")}">${x.hint ? `<div class="hint">${esc(x.hint)}</div>` : ""}</div>`;
      });
      h += `</div>`; continue;
    }
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
  // selects and checkboxes can reveal/hide dependent fields (e.g. App icon → Browse), so redraw on change
  root.querySelectorAll("select[data-k], input[type=checkbox][data-k]").forEach((el) => el.addEventListener("change", () => rerender({ ...cfg, ...readForm(root) })));
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
  if (S.stage) closeStage();  // a tab click always lands on the project's home page
  const ov = $("#overlay"); if (ov.classList.contains("dim")) { ov.className = "overlay hidden"; ov.innerHTML = ""; }
  window.scrollTo(0, 0);
  S.pid = pid; S.rid = null; try { localStorage.setItem("e2-pid", pid); } catch (e) {}
  refresh();
}

/* ── auto-trigger watch status + flash on new commit ── */
const seenChange = {};
function watchLine(w) {
  if (!w) return "";
  const next = w.checked ? Math.max(0, w.every - Math.round((Date.now() - new Date(w.checked)) / 1000)) : null;
  const res = w.paused ? "paused while a run is in progress" : w.result === "error" ? `<span style="color:var(--crit)">check failed: ${esc(w.error)}</span>`
    : w.result === "changed" ? `<b style="color:var(--ok, #22c55e)">new commit found</b>` : w.result ? "no change" : "waiting for first check";
  return `<div class="watch-line"><span class="pulse"></span>Watching GitHub every ${w.every}s · last checked ${w.checked ? esc(ago(w.checked)) : "–"} · ${res}${next !== null && !w.paused ? ` · next in ${next}s` : ""}</div>`;
}
function flashChange(pid, w) {
  if (!w?.changed) return;
  if (seenChange[pid] === undefined) { seenChange[pid] = w.changed; if (Date.now() - new Date(w.changed) > 30000) return; }
  else if (seenChange[pid] === w.changed) return;
  seenChange[pid] = w.changed;
  S.rid = w.rid || S.rid;
  let f = $("#flash"); if (!f) { f = document.createElement("div"); f.id = "flash"; document.body.appendChild(f); }
  f.className = "flash"; f.innerHTML = `⚡ Source code changed${w.commit ? ` (<span class="mono">${esc(w.commit)}</span>)` : ""}. New commit found, starting run${w.number ? " #" + w.number : ""}: Source → Build → …`;
  clearTimeout(flashChange.t); flashChange.t = setTimeout(() => { f.className = "flash hidden"; }, 8000);
}

/* ── project view ── */
async function loadProject() {
  if (!S.pid) { $("#main").innerHTML = `<div class="card"><div class="empty"><p>No projects yet.</p><button class="btn" onclick="newProject()">＋ Create your first project</button></div></div>`; return; }
  S.data = await api(`/projects/${S.pid}`);
  flashChange(S.pid, S.data.watch);
  if (!S.rid || !S.data.runs.find((r) => r.id === S.rid)) S.rid = S.data.runs[0]?.id || null;
  renderProject();
  followRun();
  if (S.stage) renderStage();
}
const curRun = () => S.data?.runs.find((r) => r.id === S.rid) || null;

function renderProject() {
  const { project: p, runs, active } = S.data, r = curRun();
  const stageOf = (k) => r ? r.stages[k] : { status: "pending" };
  const resumable = r && ["failed", "cancelled", "interrupted", "incomplete"].includes(r.status);
  const stopAt = r && S.meta.stages.find((s) => !["success", "skipped"].includes(r.stages[s.key].status));
  $("#main").innerHTML = `
  <div class="proj-head">
    <div>
      <h2>${esc(p.name)} <span class="ver">v${esc(p.version)}</span></h2>
      <div class="muted">${esc(optsLine("source", p.config.source))}</div>
      ${watchLine(S.data.watch)}
    </div>
    <div class="row">
      ${runs.length ? `<select class="form-input inline" id="run-sel" title="Choose run">${runs.map((x) => `<option value="${x.id}" ${x.id === S.rid ? "selected" : ""}>Run #${x.number} · v${esc(x.version)} · ${esc(x.status)}</option>`).join("")}</select>` : ""}
      ${r && r.status === "running" ? `<button class="btn danger" id="cancel-btn">■ Cancel</button>` : ""}
      ${resumable && !active ? `<button class="btn warn" id="resume-btn" title="Continue run #${r.number} from the stage where it stopped">▶ Continue build${stopAt ? " · from " + esc(stopAt.label) : ""}</button>` : ""}
      <select class="form-input inline" id="bump">
        <option value="patch">Patch → ${esc(nextV(p.version, "patch"))}</option>
        <option value="minor">Minor → ${esc(nextV(p.version, "minor"))}</option>
        <option value="major">Major → ${esc(nextV(p.version, "major"))}</option>
        <option value="none">Rebuild ${esc(p.version)}</option>
        <option value="custom">Custom version…</option>
      </select>
      <button class="btn ${resumable ? "ghost" : ""}" id="run-btn" ${active ? "disabled" : ""} title="${runs.length ? "Start a new run from stage 1" : "Start the first run"}">${runs.length ? "⟲ Re-build" : "▶ Run pipeline"}</button>
    </div>
  </div>
  <div class="pipeline">${S.meta.stages.map((s, i) => { const st = stageOf(s.key).status; return `${i ? `<span class="link ${st === "success" || st === "skipped" ? "done" : ""}"></span>` : ""}<span class="chip s-${st}" data-open="${s.key}"><span class="sdot s-${st}"></span>${esc(s.label)}</span>`; }).join("")}</div>
  ${r ? `<div class="muted" style="margin:-8px 0 12px;font-size:12px">Run #${r.number} · ${esc(r.trigger)} · v${esc(r.version)} · ${badge(r.status)} · started ${esc(ago(r.started || r.created))}${r.started ? " · " + dur(r.started, r.finished) : ""}</div>` : ""}
  <div class="stages">${S.meta.stages.map((s, i) => {
    const st = stageOf(s.key);
    return `<div class="stage s-${st.status}" data-open="${s.key}">
      <div class="rail"></div><div class="num">${ICON[st.status] || i + 1}</div>
      <div class="body">
        <div class="title">${i + 1}. ${esc(s.label)} ${badge(st.status)}</div>
        <div class="sum">${esc(st.summary || (r ? "" : "Not run yet"))}</div>
        <div class="opts">${esc(optsLine(s.key, p.config[s.key]))}</div>
      </div>
      <div class="side">${r && !active ? `<button class="btn ghost sm" data-stage-run="${s.key}" title="Run only this stage again (uses current options)">↻ Run stage</button><br>` : ""}${st.started ? esc(dur(st.started, st.finished)) : ""}${st.finished ? " · " + esc(ago(st.finished)) : ""}</div>
    </div>`; }).join("")}</div>
  <div class="card" style="margin-top:16px"><div class="card-h"><h3>Versions &amp; runs</h3><span class="muted" style="font-size:12px">Click a row to inspect that run</span></div>
    ${runs.length ? `<div style="overflow-x:auto"><table><tr><th>Run</th><th>Version</th><th>Status</th><th>Trigger</th><th>Commit</th><th>Started</th><th>Duration</th></tr>
    ${runs.map((x) => `<tr class="clk ${x.id === S.rid ? "sel" : ""}" data-rid="${x.id}"><td class="mono">#${x.number}</td><td class="mono">v${esc(x.version)}</td><td>${badge(x.status)}</td><td>${esc(x.trigger)}</td><td class="mono">${esc((x.stages.source.detail?.commit || "").slice(0, 8))}</td><td>${esc(ago(x.created))}</td><td class="mono">${esc(dur(x.started, x.finished))}</td></tr>`).join("")}</table></div>` : `<div class="empty">No runs yet. Click <b>Run pipeline</b>.</div>`}
  </div>
  ${(p.releases || []).length ? `<div class="card" style="margin-top:16px"><div class="card-h"><h3>Releases</h3></div><div style="overflow-x:auto"><table><tr><th>Version</th><th>Run</th><th>Destination</th><th>Where</th><th>When</th></tr>
    ${p.releases.map((x) => `<tr><td class="mono">v${esc(x.version)}</td><td class="mono">#${x.run}</td><td>${esc(x.destination)}</td><td class="mono">${/^(\/|[A-Za-z]:\\)/.test(x.where || "") ? `<a href="#" class="reveal" data-path="${esc(x.where)}" title="Open in Finder">📂 ${esc(x.where)}</a>` : esc(x.where)}</td><td>${esc(ago(x.at))}</td></tr>`).join("")}</table></div></div>` : ""}
  <div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn ghost sm" id="exp-btn" title="Download the whole pipeline configuration as one JSON file">⇩ Export config</button><button class="btn ghost sm" id="del-btn">Delete project</button></div>`;

  $("#main").querySelectorAll("[data-open]").forEach((el) => el.onclick = () => { S.stage = el.dataset.open; S.logOff = 0; S.logRid = null; renderStage(); });
  $("#main").querySelectorAll("[data-rid]").forEach((el) => el.onclick = () => { S.rid = el.dataset.rid; renderProject(); });
  if ($("#run-sel")) $("#run-sel").onchange = (e) => { S.rid = e.target.value; renderProject(); };
  $("#run-btn").onclick = startRun;
  if ($("#cancel-btn")) $("#cancel-btn").onclick = () => act(`/projects/${S.pid}/runs/${S.rid}/cancel`, "Cancelling…");
  if ($("#resume-btn")) $("#resume-btn").onclick = () => act(`/projects/${S.pid}/runs/${S.rid}/resume`, "Continuing from where it stopped");
  $("#main").querySelectorAll("[data-stage-run]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); stageRun(b.dataset.stageRun, "only"); });
  $("#exp-btn").onclick = exportConfig;
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
  syncTop();
  const ov = $("#overlay"), cfg = S.data.project.config[key];
  const keepForm = ov.dataset.stage === key && ov.dataset.rid === String(S.rid) && !ov.classList.contains("hidden");
  const det = st?.detail || {};
  const info = `<dl class="kv">
      <dt>Status</dt><dd>${st ? badge(st.status) : "–"} ${meta.implemented ? "" : `<span class="badge soon">coming next</span>`}</dd>
      ${r ? `<dt>Run</dt><dd>#${r.number} · v${esc(r.version)} · ${esc(r.trigger)}</dd>` : ""}
      ${st?.started ? `<dt>Duration</dt><dd>${esc(dur(st.started, st.finished))}</dd>` : ""}
      ${st?.summary ? `<dt>Summary</dt><dd>${esc(st.summary)}</dd>` : ""}
      ${Object.entries(det).filter(([k, v]) => v !== null && v !== "" && k !== "fingerprint").map(([k, v]) => `<dt>${esc(k)}</dt><dd class="${["commit", "path", "url", "where"].includes(k) ? "mono" : ""}">${["where", "path"].includes(k) && typeof v === "string" && v.startsWith("/") ? `<a href="#" class="reveal" data-path="${esc(v)}" title="Open in Finder">📂 ${esc(v)}</a>` : fmtVal(v)}</dd>`).join("")}
    </dl><div id="arts"></div>`;
  if (keepForm) {
    if ($("#stage-info").dataset.sig !== JSON.stringify(st)) { $("#stage-info").innerHTML = info; $("#stage-info").dataset.sig = JSON.stringify(st); loadArts(); }
    $("#ov-status").innerHTML = st ? badge(st.status) : "";
    ov.querySelectorAll("[data-go]").forEach((b) => b.classList.toggle("s-running", r?.stages[b.dataset.go]?.status === "running")); $("#stage-acts").innerHTML = stageActs(r); pollLog(); return;
  }
  ov.dataset.stage = key; ov.dataset.rid = S.rid;
  ov.className = "overlay";
  ov.innerHTML = `
    <div class="ov-h"><button class="btn ghost sm" id="ov-close">← Back</button><h2>${S.meta.stages.indexOf(meta) + 1}. ${esc(meta.label)}</h2><span id="ov-status">${st ? badge(st.status) : ""}</span><span class="spacer" style="flex:1"></span>
      ${S.meta.stages.map((s) => `<button class="btn sm ${s.key === key ? "" : "ghost"} ${r?.stages[s.key]?.status === "running" ? "s-running" : ""}" data-go="${s.key}">${esc(s.label)}</button>`).join("")}</div>
    <div class="ov-b">
      <div class="card"><div class="card-h"><h3>Details &amp; options</h3></div><div class="card-b">
        <div id="stage-info">${info}</div>
        <div class="row" id="stage-acts" style="margin-bottom:12px">${stageActs(r)}</div>
        <div id="stage-form"></div>
        <div class="row" style="margin-top:14px"><button class="btn" id="save-cfg">Save options</button><span class="hint">Applies to the next run.</span></div>
        ${key === "source" ? `<div id="changes"></div>` : ""}
        ${key === "platform" ? `<div class="group-h" style="margin-top:16px">Windows builder (GitHub Actions)</div>
          <div class="hint" style="margin-bottom:6px">One-time: commits <span class="mono">.github/workflows/e2-windows.yml</span> to the repository. It only runs when E2 starts it, never on push. Click again after changing the app options.</div>
          <button class="btn ghost sm" id="win-builder">Install Windows builder</button>` : ""}
        ${key === "build" ? `<div id="analysis"><div class="group-h">Dependencies</div><div class="hint">Scanning…</div></div>` : ""}
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
  if ($("#win-builder")) $("#win-builder").onclick = async () => {
    if (!confirm(`This commits and pushes .github/workflows/e2-windows.yml to ${S.data.project.config.source.url} (${S.data.project.config.source.branch}). Continue?`)) return;
    try { const j = await api(`/projects/${S.pid}/windows-builder`, { method: "POST" }); toast(j.message); } catch (e) { toast(e.message, true); }
  };
  $("#stage-acts").onclick = (e) => { const b = e.target.closest("[data-mode]"); if (b) stageRun(key, b.dataset.mode); };
  $("#stage-info").dataset.sig = JSON.stringify(st);
  S.logOff = 0; S.logRid = S.rid; pollLog(); loadArts(); loadAnalysis(); loadChanges();
}
function stageActs(r) {
  if (!r) return `<span class="hint">Run the pipeline once to enable per-stage runs.</span>`;
  if (S.data.active) return `<span class="hint">A run is in progress…</span>`;
  return `<button class="btn sm" data-mode="only" title="Run just this stage again on run #${r.number}">↻ Run this stage</button>
    <button class="btn ghost sm" data-mode="from" title="Re-run this stage and every stage after it">⏭ Re-run from here</button>
    <span class="hint">Uses the options as saved now.</span>`;
}
async function stageRun(key, mode) {
  try {
    await api(`/projects/${S.pid}/runs/${S.rid}/stage/${key}`, { method: "POST", body: { mode } });
    if (S.stage === key) { S.logOff = 0; const l = $("#log"); if (l) l.textContent = ""; }
    if (mode === "from") S.follow = { rid: S.rid, from: key };
    toast(mode === "only" ? "Running this stage again" : "Re-running from this stage"); refresh();
  } catch (e) { toast(e.message, true); }
}
function fmtVal(v) {
  if (Array.isArray(v)) return v.map(esc).join("<br>");
  if (v && typeof v === "object") return Object.entries(v).map(([k, x]) => {
    if (x && typeof x === "object") { const st = x.status || ""; const rest = x.note || (x.artifacts || []).join(", "); return `<div><b>${esc(k)}</b> <span class="badge s-${esc({ built: "success", passed: "success", warning: "cancelled", unsupported: "skipped" }[st] || st)}">${esc(st)}</span> <span class="muted">${esc(rest)}</span></div>`; }
    return `<div><b>${esc(k)}</b> <span class="muted">${esc(x)}</span></div>`;
  }).join("");
  return esc(v);
}
async function loadArts() {
  const el = $("#arts"); if (!el || !S.rid || !["platform", "verify", "publish", "test"].includes(S.stage)) return;
  try {
    const j = await api(`/projects/${S.pid}/runs/${S.rid}/artifacts`), base = `/api/projects/${S.pid}/runs/${S.rid}`;
    const kb = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : (n / 1024).toFixed(1) + " KB";
    el.innerHTML = (S.stage === "test" ? "" : j.artifacts.length ? `<div class="group-h">Artifacts</div>` + j.artifacts.map((a) => `<div class="mono" style="font-size:12px"><a href="${base}/artifacts/${a.path.split("/").map(encodeURIComponent).join("/")}">${esc(a.path)}</a> <span class="muted">${kb(a.size)}</span></div>`).join("") : "")
      + (S.stage === "test" && j.reports.length ? `<div class="group-h">Reports</div>` + j.reports.map((n) => `<div><a href="${base}/reports/${n}" target="_blank">${esc(n)} report (JSON)</a></div>`).join("") : "");
  } catch (e) { /* ignore */ }
}
function syncTop() { const t = document.querySelector(".topbar"); if (t) document.documentElement.style.setProperty("--top-h", t.offsetHeight + "px"); }
window.addEventListener("resize", syncTop);
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
    <div class="import-box"><label class="btn ghost sm" style="cursor:pointer">⇧ Import config file<input type="file" id="np-file" accept=".json,application/json" hidden></label>
      <span class="hint" id="np-imp">Optional: start from an exported <span class="mono">*.e2.json</span> pipeline (all stages and options).</span></div>
    <div class="group-h">Stage 1 · Source</div><div id="np-form"></div>
    <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn ghost" id="np-cancel">Cancel</button><button class="btn" id="np-ok">Create project</button></div>
  </div></div>`;
  const draw = (c) => { cfg = c; $("#np-form").innerHTML = formHTML("source", c, "n"); wireForm($("#np-form"), "source", c, draw); };
  draw(cfg);
  let imported = null;
  $("#np-file").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      if (j.format !== "e2-pipeline" || !j.config || typeof j.config !== "object") throw new Error("Not an E2 pipeline file (expected \"format\": \"e2-pipeline\")");
      imported = j.config;
      if (j.name) $("#np-name").value = j.name + " (copy)";
      if (j.version) $("#np-ver").value = j.version;
      draw({ ...S.meta.defaults.source, ...(j.config.source || {}) });
      $("#np-imp").innerHTML = `Loaded <b>${esc(f.name)}</b>: ${Object.keys(j.config).length} stage(s) configured. You can adjust Source below.`;
    } catch (err) { toast("Import failed: " + err.message, true); }
  };
  const close = () => { ov.className = "overlay hidden"; ov.innerHTML = ""; };
  $("#np-x").onclick = $("#np-cancel").onclick = close;
  $("#np-ok").onclick = async () => {
    try {
      const p = await api("/projects", { method: "POST", body: { name: $("#np-name").value, version: $("#np-ver").value, config: { ...(imported || {}), source: { ...cfg, ...readForm($("#np-form")) } } } });
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
  document.documentElement.style.setProperty("--phase", `-${Date.now() % 7200}ms`);
  const busy = S.overview?.counts.running > 0;
  refresh().finally(() => setTimeout(tick, busy ? 1500 : 5000));
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.stage) closeStage(); });
$("#theme-btn").onclick = () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; try { localStorage.setItem("e2-theme", t); } catch (e) {} };
(async () => { S.meta = await api("/meta"); tick(); setInterval(pollLog, 1000); })();

/* ── build page: top dependencies + OS-specific code ── */
const fsz = (n) => n == null ? "–" : n > 1073741824 ? (n / 1073741824).toFixed(2) + " GB" : n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : (n / 1024).toFixed(1) + " KB";
async function loadAnalysis() {
  const el = $("#analysis"); if (!el || S.stage !== "build") return;
  const pid = S.pid;
  try {
    const a = await api(`/projects/${pid}/analysis`);
    if (!$("#analysis") || S.pid !== pid) return;
    const max = Math.max(1, ...a.dependencies.map((d) => d.size || 0));
    const top = a.dependencies.slice(0, 25);
    const c = a.code.counts, f = a.code.files;
    const cats = Object.keys(c);
    el.innerHTML = `
      ${a.services?.length ? `<div class="group-h">Databases &amp; services used by the code</div>
      <table class="dep-t"><tr><th>Component</th><th>What it means</th><th>Used in</th></tr>${a.services.map((x) => `<tr><td><b>${esc(x.name)}</b></td><td class="muted">${esc(x.note)}</td><td class="mono" title="${esc(x.files.join("\n"))}">${x.count} file(s)</td></tr>`).join("")}</table>` : ""}
      <div class="group-h" style="margin-top:16px">Top dependencies · ${a.total} found · ${fsz(a.total_size)} total</div>
      ${top.length ? `<table class="dep-t"><tr><th>#</th><th>Name</th><th>Size</th><th>OS / platform</th></tr>${top.map((d, i) => `<tr><td class="muted">${i + 1}</td><td><b>${esc(d.name)}</b> <span class="muted">${esc(d.version)}</span>${d.direct ? "" : ' <span class="muted">(indirect)</span>'}<div class="dep-bar" style="width:${Math.max(1, (d.size || 0) / max * 100)}%"></div></td><td class="mono">${fsz(d.size)}</td><td>${d.platforms.length ? d.platforms.map((p) => `<span class="ptag">${esc(p)}</span>`).join("") : '<span class="muted">any</span>'}${d.note ? `<div class="muted" style="font-size:10px">${esc(d.note)}</div>` : ""}</td></tr>`).join("")}</table>
      ${a.os_specific.length ? `<div class="hint" style="margin-top:6px">OS-specific packages: ${a.os_specific.map(esc).join(", ")}</div>` : ""}` : `<div class="hint">No dependencies found for ${esc(a.tool || "this project")}.</div>`}
      <div class="group-h" style="margin-top:16px">OS-specific code in the source</div>
      <div class="row" style="flex-wrap:wrap;gap:6px;margin-bottom:6px">${cats.map((k) => `<button class="btn sm ${c[k] ? "cat-on" : "ghost cat-off"}" data-cat="${esc(k)}" title="${c[k] ? "Referenced in the code" : "Not referenced"}">${c[k] ? "● " : ""}${esc(k)} · ${c[k]} in ${f[k]} file(s)</button>`).join("")}</div>
      <div id="hits"></div>`;
    const show = (cat) => { const h = a.code.hits.filter((x) => !cat || x.cat === cat);
      $("#hits").innerHTML = h.length ? h.slice(0, 150).map((x) => `<div class="hit"><span class="ptag">${esc(x.cat)}</span><span class="f">${esc(x.file)}:${x.line}</span> ${esc(x.text)}</div>`).join("") + (a.code.truncated ? `<div class="hint">List truncated.</div>` : "") : `<div class="hint">Nothing found${cat ? " for " + esc(cat) : ""}.</div>`; };
    el.querySelectorAll("[data-cat]").forEach((b) => b.onclick = () => { el.querySelectorAll("[data-cat]").forEach((x) => x.classList.toggle("cat-sel", x === b)); show(b.dataset.cat); });
    const first = cats.find((k) => c[k]); el.querySelector(`[data-cat="${first}"]`)?.classList.add("cat-sel"); show(first);
  } catch (e) { if ($("#analysis")) $("#analysis").innerHTML = `<div class="group-h">Dependencies</div><div class="hint">${esc(e.message)}</div>`; }
}

/* ── source page: what was picked up and what changed ── */
async function loadChanges() {
  const el = $("#changes"); if (!el || S.stage !== "source" || !S.rid) return;
  const pid = S.pid, rid = S.rid;
  try {
    const c = await api(`/projects/${pid}/runs/${rid}/changes`);
    if (!$("#changes") || S.rid !== rid) return;
    const [subj, ...body] = c.message.split("\n");
    el.innerHTML = `<div class="group-h" style="margin-top:16px">Picked up</div>
      <dl class="kv"><dt>App version</dt><dd>${c.version ? "v" + esc(c.version) : "–"}</dd>
      <dt>Commit</dt><dd class="mono">${esc(c.commit.slice(0, 10))}</dd><dt>Author</dt><dd>${esc(c.author)} · ${esc(ago(c.date))}</dd></dl>
      <div style="font-weight:600;margin-bottom:4px">${esc(subj)}</div>
      ${body.join("\n").trim() ? `<pre class="commit-msg">${esc(body.join("\n").trim())}</pre>` : ""}
      <div class="group-h" style="margin-top:12px">${c.previous ? `Changes since last build (${esc(c.previous.slice(0, 8))})` : "Changes in this commit"} · ${c.commits.length} commit(s) · ${c.file_count} file(s)</div>
      ${c.commits.map((x) => `<div class="hit"><span class="f">${esc(x.sha)}</span> ${esc(x.subject)} <span class="muted">· ${esc(x.author)}</span></div>`).join("")}
      <div style="margin-top:8px">${c.files.map((f) => `<div class="hit"><span style="color:#22c55e">+${esc(f.add)}</span> <span style="color:var(--crit)">-${esc(f.del)}</span> ${esc(f.file)}</div>`).join("")}</div>`;
  } catch (e) { if ($("#changes")) $("#changes").innerHTML = `<div class="group-h" style="margin-top:16px">Picked up</div><div class="hint">${esc(e.message)}</div>`; }
}

/* ── export the whole pipeline configuration as one JSON file ── */
function exportConfig() {
  const p = S.data.project;
  const doc = { format: "e2-pipeline", schema: 1, exported: new Date().toISOString(), name: p.name, version: p.version,
    stages: S.meta.stages.map((s) => s.key), config: p.config };
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2) + "\n"], { type: "application/json" }));
  a.download = `${p.name.replace(/[^\w.-]+/g, "-").toLowerCase()}.e2.json`;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast("Pipeline config exported");
}

/* click a local path → open it in Finder */
document.addEventListener("click", async (e) => {
  const a = e.target.closest(".reveal"); if (!a) return;
  e.preventDefault(); e.stopPropagation();
  try { await api("/reveal", { method: "POST", body: { path: a.dataset.path } }); } catch (err) { toast(err.message, true); }
});

/* after "Re-run from here": move the stage page along with the run, then report how it ended */
function followRun() {
  const f = S.follow, r = curRun();
  if (!f || !r || r.id !== f.rid) return;
  const running = S.meta.stages.find((s) => r.stages[s.key].status === "running");
  if (running && S.stage && S.stage !== running.key) { S.stage = running.key; S.logOff = 0; S.logRid = null; $("#overlay").dataset.stage = ""; }
  if (["running", "queued"].includes(r.status)) return;
  S.follow = null;
  const keys = S.meta.stages.map((s) => s.key), done = keys.slice(keys.indexOf(f.from));
  const line = done.map((k) => `${S.meta.stages.find((s) => s.key === k).label} ${r.stages[k].status === "success" ? "✓" : r.stages[k].status === "failed" ? "✕" : "–"}`).join(" → ");
  const last = done.filter((k) => r.stages[k].status !== "pending").pop();
  if (S.stage && last && S.stage !== last) { S.stage = last; S.logOff = 0; S.logRid = null; $("#overlay").dataset.stage = ""; }
  toast(`Run #${r.number} ${r.status}: ${line}`, r.status !== "success");
}
