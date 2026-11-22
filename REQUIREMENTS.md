# E2: local CI/CD pipeline. Requirements and plan

Python + FastAPI backend with a vanilla-JS dashboard (same stack and look as Wolf). Runs locally, used from the browser.
Start: `./run.sh` → http://localhost:8700 (uses the next free port if 8700 is taken).

## Pipeline stages (each run goes through them in order)
| # | Stage | Status |
|---|-------|--------|
| 1 | **Source**: clone/pull a Git branch (optionally pinned to a commit/tag) or copy a local folder; detect technology | ✅ |
| 2 | **Build & Dependencies**: build with the detected/selected tool (Maven, Gradle, npm, Python, Go, .NET, Rust, Flutter, Xcode, custom) and download dependencies | ✅ |
| 3 | **Target Platform**: Desktop: macOS, Windows, Linux · Mobile: iOS, Android | ✅ |
| 4 | **Test** (optional checkboxes): unit, integration, regression, in Docker, security review, license/patent/copied-code review | ✅ |
| 5 | **Verify Artifact**: checksum, code signing, smoke test | ✅ |
| 6 | **Publish**: local folder, Artifactory/Nexus, GitHub Releases, Docker registry, App Store Connect/TestFlight, Google Play, Microsoft Store | ✅ |

Machines can't build every target. A target this machine can't build (e.g. Windows Flutter on a Mac, Go for iOS) is reported as *unsupported* rather than failed.
Go and .NET cross-compile for all desktop OSes. JVM, Python and web builds produce one portable artifact.
Test checks report passed / failed / skipped / warning. License review only *warns*; a person decides.
Security fails on real secrets or audit failures. Reports go in `runs/<n>/reports/`.
Publish credentials come from environment variables (see `e2/stages/publish.py`), never project files.

## Running and re-running
- **Run pipeline** (first run) → afterwards **Re-build**: a new run from stage 1 with a version bump.
- **Continue build**: carries on the selected run from the stage where it stopped.
- Per stage: **Run this stage** or **Re-run from here**. Both use the options as saved now, so a fix applies straight away.

## Triggers & versions
- Trigger per project: **manual** (Run button) or **automatic** (polls the branch head / local folder and runs on change).
- Versions are `MAJOR.MINOR.PATCH`. Each run chooses patch/minor/major bump, rebuild current, or a custom version.
- Automatic runs bump the patch version.
- Every run is kept with its version, commit and status. Later stages will let you pick which version to deploy or release.

## State & logs (simple file storage for now; DBMS later if needed)
`data/projects/<project>/`: `project.json`, `workspace/`, `runs/<n>/run.json` (per-stage state),
`runs/<n>/events.jsonl` (every transition), `runs/<n>/logs/<stage>.log`.
State is saved after every transition. If the server stops mid-run, the run is marked *interrupted*.
**Continue build** picks up from the first stage that did not finish.

## UI
- Top strip (~10%): project count, running, succeeded, failed, needs attention, auto-trigger.
- Projects shown as tabs, with a dropdown when there are several.
- Project body: a pipeline strip, then one vertical pane per stage with status, summary and options. Click a pane to open the stage full screen: details, editable options, and a live log.
