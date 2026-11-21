# E2: local CI/CD pipeline. Requirements and plan

Python + FastAPI backend with a vanilla-JS dashboard (same stack and look as Wolf). Runs locally, used from the browser.
Start: `./run.sh` → http://localhost:8700 (uses the next free port if 8700 is taken).

## Pipeline stages (each run goes through them in order)
| # | Stage | Status |
|---|-------|--------|
| 1 | **Source**: clone/pull a Git branch (optionally pinned to a commit/tag) or copy a local folder; detect technology | ✅ built |
| 2 | **Build & Dependencies**: build with the detected/selected tool (Maven, Gradle, npm, Python, Go, .NET, Rust, Flutter, Xcode, custom) and download dependencies | next |
| 3 | **Target Platform**: Desktop: macOS, Windows, Linux · Mobile: iOS, Android | planned |
| 4 | **Test** (optional checkboxes): unit, integration, regression, in Docker, security review, license/patent/copied-code review | planned |
| 5 | **Verify Artifact**: checksum, code signing, smoke test | planned |
| 6 | **Publish**: local folder, Artifactory/Nexus, GitHub Releases, Docker registry, App Store Connect/TestFlight, Google Play, Microsoft Store | planned |

Stages not built yet are shown and their options can already be set and saved. During a run they are marked *skipped*.

## Triggers & versions
- Trigger per project: **manual** (Run button) or **automatic** (polls the branch head / local folder and runs on change).
- Versions are `MAJOR.MINOR.PATCH`. Each run chooses patch/minor/major bump, rebuild current, or a custom version.
- Automatic runs bump the patch version.
- Every run is kept with its version, commit and status. Later stages will let you pick which version to deploy or release.

## State & logs (simple file storage for now; DBMS later if needed)
`data/projects/<project>/`: `project.json`, `workspace/`, `runs/<n>/run.json` (per-stage state),
`runs/<n>/events.jsonl` (every transition), `runs/<n>/logs/<stage>.log`.
State is saved after every transition. If the server stops mid-run, the run is marked *interrupted*.
**Resume** continues from the first stage that did not finish, and also works on failed or cancelled runs.

## UI
- Top strip (~10%): project count, running, succeeded, failed, needs attention, auto-trigger.
- Projects shown as tabs, with a dropdown when there are several.
- Project body: a pipeline strip, then one vertical pane per stage with status, summary and options. Click a pane to open the stage full screen: details, editable options, and a live log.
