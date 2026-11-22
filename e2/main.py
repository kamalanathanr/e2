"""E2 - FastAPI app: JSON API + single-page dashboard."""
import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import runner, store
from .stages import source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("e2")
HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    n = store.mark_interrupted()
    if n:
        log.info("%d run(s) were interrupted by the last shutdown; they can be resumed", n)
    poll = asyncio.create_task(runner.poller())
    yield
    poll.cancel()


app = FastAPI(title="E2", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


def _project(pid: str) -> dict:
    p = store.project(pid)
    if not p:
        raise HTTPException(404, "No such project")
    return p


def _run(pid: str, rid: str) -> dict:
    r = store.run(pid, rid)
    if not r:
        raise HTTPException(404, "No such run")
    return r


@app.get("/api/meta")
def meta():
    return {"stages": [{"key": k, "label": l, "implemented": k in runner.HANDLERS} for k, l in store.STAGES],
            "defaults": store.DEFAULT_CONFIG, "data_dir": str(store.DATA)}


@app.get("/api/overview")
def overview():
    out = []
    for p in store.projects():
        rs = store.runs(p["id"], limit=1)
        out.append({"id": p["id"], "name": p["name"], "version": p["version"],
                    "trigger": p["config"]["source"]["trigger"], "source_type": p["config"]["source"]["type"],
                    "last_run": rs[0] if rs else None})
    st = [x["last_run"]["status"] for x in out if x["last_run"]]
    return {"projects": out, "counts": {"projects": len(out), "running": st.count("running"),
                                        "success": st.count("success"), "failed": st.count("failed"),
                                        "attention": sum(s in ("interrupted", "cancelled", "incomplete") for s in st),
                                        "auto": sum(x["trigger"] == "auto" for x in out)}}


@app.post("/api/projects")
def create_project(b: dict = Body(...)):
    name = str(b.get("name", "")).strip()
    version = str(b.get("version") or "0.1.0").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if not store.VERSION_RE.match(version):
        raise HTTPException(400, "Version must be MAJOR.MINOR.PATCH")
    return store.create_project(name, b.get("config") or {}, version)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return {"project": _project(pid), "runs": store.runs(pid, limit=50), "active": runner.active(pid),
            "watch": runner.watch_info(pid)}


@app.put("/api/projects/{pid}")
def update_project(pid: str, b: dict = Body(...)):
    _project(pid)
    fields = {}
    if "name" in b:
        fields["name"] = str(b["name"]).strip() or _project(pid)["name"]
    if "config" in b:
        fields["config"] = {k: v for k, v in b["config"].items() if k in store.STAGE_KEYS}
    if "version" in b:
        if not store.VERSION_RE.match(str(b["version"])):
            raise HTTPException(400, "Version must be MAJOR.MINOR.PATCH")
        fields["version"] = b["version"]
    return store.update_project(pid, **fields)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _project(pid)
    if runner.active(pid):
        raise HTTPException(409, "Cancel the running pipeline first")
    store.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/runs")
async def start_run(pid: str, b: dict = Body(default={})):
    _project(pid)
    if runner.active(pid):
        raise HTTPException(409, "A run is already in progress for this project")
    try:
        r = store.create_run(pid, trigger="manual", bump=b.get("bump", "patch"), custom=b.get("version", ""))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    runner.start(pid, r["id"])
    return r


@app.get("/api/projects/{pid}/runs/{rid}")
def get_run(pid: str, rid: str):
    return _run(pid, rid)


RESUMABLE = ("failed", "cancelled", "interrupted", "incomplete")


def _fresh_config(pid: str, r: dict) -> None:
    """Retries use the options as saved now, so a fix made after a failure takes effect."""
    r["config"] = _project(pid)["config"]


@app.post("/api/projects/{pid}/runs/{rid}/resume")
async def resume_run(pid: str, rid: str):
    """Continue build: pick up at the first stage that has not finished."""
    r = _run(pid, rid)
    if runner.active(pid):
        raise HTTPException(409, "A run is already in progress for this project")
    if r["status"] not in RESUMABLE:
        raise HTTPException(400, f"Run is {r['status']}; nothing to continue")
    _fresh_config(pid, r)
    store.save_run(r)
    runner.start(pid, rid, resume=True)
    return {"ok": True}


@app.post("/api/projects/{pid}/runs/{rid}/stage/{stage}")
async def run_stage(pid: str, rid: str, stage: str, b: dict = Body(default={})):
    """Run one stage again ("only") or re-run from it through the end ("from")."""
    r = _run(pid, rid)
    if stage not in store.STAGE_KEYS:
        raise HTTPException(404, "No such stage")
    if runner.active(pid):
        raise HTTPException(409, "A run is already in progress for this project")
    mode = b.get("mode", "only")
    i = store.STAGE_KEYS.index(stage)
    for k in store.STAGE_KEYS[i:] if mode == "from" else [stage]:
        r["stages"][k].update(status="pending", started=None, finished=None, summary="", detail={})
    _fresh_config(pid, r)
    store.save_run(r)
    store.event(pid, rid, stage, f"rerun-{mode}")
    store.log(pid, rid, stage, f"── re-run requested ({'this stage only' if mode == 'only' else 'from this stage on'}) ──")
    runner.start(pid, rid, resume=True, only=stage if mode == "only" else None)
    return {"ok": True}


@app.get("/api/projects/{pid}/runs/{rid}/artifacts")
def list_artifacts(pid: str, rid: str):
    _run(pid, rid)
    art = store.rdir(pid, rid) / "artifacts"
    files = sorted(p for p in art.rglob("*") if p.is_file()) if art.exists() else []
    rep = store.rdir(pid, rid) / "reports"
    return {"artifacts": [{"path": str(p.relative_to(art)), "size": p.stat().st_size} for p in files],
            "reports": sorted(p.stem for p in rep.glob("*.json")) if rep.exists() else []}


@app.get("/api/projects/{pid}/runs/{rid}/artifacts/{path:path}")
def get_artifact(pid: str, rid: str, path: str):
    _run(pid, rid)
    art = (store.rdir(pid, rid) / "artifacts").resolve()
    f = (art / path).resolve()
    if not f.is_relative_to(art) or not f.is_file():
        raise HTTPException(404, "No such artifact")
    return FileResponse(f, filename=f.name)


@app.get("/api/projects/{pid}/runs/{rid}/changes")
async def changes(pid: str, rid: str):
    """What this run picked up: app version, full commit message, and commits/files since the previous build."""
    from .stages import deps
    r = _run(pid, rid)
    sha = (r["stages"]["source"].get("detail") or {}).get("commit")
    ws = store.workspace(pid)
    if not sha or not (ws / ".git").exists():
        raise HTTPException(404, "No commit recorded for this run")
    prev = next((x["stages"]["source"].get("detail", {}).get("commit") for x in store.runs(pid, limit=200)
                 if x["number"] < r["number"] and x["stages"]["source"].get("detail", {}).get("commit") not in (None, sha)), None)
    return await asyncio.to_thread(deps.changes, ws, sha, prev)


@app.post("/api/projects/{pid}/windows-builder")
async def windows_builder(pid: str):
    """One-time: commit E2's Windows build workflow to the project's GitHub repository and push it."""
    from .stages import pyapp
    p = _project(pid)
    src = p["config"]["source"]
    ws = store.workspace(pid)
    if not pyapp.github_repo(src.get("url", "")) or not (ws / ".git").exists():
        raise HTTPException(400, "Needs a github.com Git source that has been checked out by a run")
    if runner.active(pid):
        raise HTTPException(400, "Wait for the current run to finish")
    app_name = (p["config"]["platform"].get("app_name") or "").strip() or p["name"]
    try:
        f = pyapp.install_workflow(ws, p["config"], app_name)
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    async def git(*a):
        proc = await asyncio.create_subprocess_exec("git", "-C", str(ws), *a, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        out, _ = await proc.communicate()
        if proc.returncode:
            raise HTTPException(400, f"git {a[0]} failed: {out.decode()[-400:]}")
        return out.decode()
    branch = src.get("branch") or "main"
    await git("fetch", "origin", branch)
    await git("checkout", "-f", "-B", branch, f"origin/{branch}")
    f = pyapp.install_workflow(ws, p["config"], app_name)  # re-write after checkout
    await git("add", pyapp.WORKFLOW)
    if not (await git("status", "--porcelain", pyapp.WORKFLOW)).strip():
        return {"ok": True, "message": "Windows builder is already up to date"}
    await git("commit", "-m", "Add E2 Windows build workflow (manual dispatch only)", "--", pyapp.WORKFLOW)
    await git("push", "origin", f"HEAD:{branch}")
    return {"ok": True, "message": f"Pushed {pyapp.WORKFLOW} to {branch}"}


@app.post("/api/reveal")
def reveal(b: dict = Body(...)):
    """Open a folder from E2's own data directory in Finder / Explorer / the file manager."""
    import subprocess
    import sys
    p = Path(str(b.get("path", ""))).expanduser().resolve()
    if not p.is_relative_to(store.DATA):
        raise HTTPException(403, "Only folders inside E2's data directory can be opened")
    if not p.exists():
        raise HTTPException(404, f"Not found: {p}")
    if sys.platform == "darwin":
        cmd = ["open", str(p)] if p.is_dir() else ["open", "-R", str(p)]
    elif sys.platform == "win32":
        cmd = ["explorer", str(p) if p.is_dir() else f"/select,{p}"]
    else:
        cmd = ["xdg-open", str(p if p.is_dir() else p.parent)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


@app.get("/api/projects/{pid}/analysis")
async def analysis(pid: str):
    from .stages import deps, tech
    ws = store.workspace(pid)
    if not ws.exists() or not any(ws.iterdir()):
        raise HTTPException(404, "Workspace is empty. Run the Source stage first")
    cfg = _project(pid).get("config", {}).get("build", {})
    return await asyncio.to_thread(deps.analyze, ws, tech.pick(ws, cfg.get("technology")))


@app.get("/api/projects/{pid}/runs/{rid}/reports/{name}")
def get_report(pid: str, rid: str, name: str):
    _run(pid, rid)
    if name not in ("security", "license"):
        raise HTTPException(404, "No such report")
    f = store.rdir(pid, rid) / "reports" / f"{name}.json"
    if not f.exists():
        raise HTTPException(404, "Report not generated for this run")
    return FileResponse(f, media_type="application/json")


@app.post("/api/projects/{pid}/runs/{rid}/cancel")
def cancel_run(pid: str, rid: str):
    if not runner.cancel(pid, rid):
        raise HTTPException(400, "Run is not in progress")
    return {"ok": True}


@app.get("/api/projects/{pid}/runs/{rid}/logs/{stage}")
def get_log(pid: str, rid: str, stage: str, offset: int = 0):
    _run(pid, rid)
    try:
        text, off = store.read_log(pid, rid, stage, max(0, offset))
    except KeyError:
        raise HTTPException(404, "No such stage")
    return {"text": text, "offset": off}


@app.get("/api/branches")
async def branches(url: str):
    try:
        heads = await source.remote_heads(url)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return {"branches": sorted(heads)}


def _free_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found")


def run():
    import uvicorn
    port = _free_port(int(os.environ.get("E2_PORT", 8700)))
    log.info("E2 on http://localhost:%d  (data: %s)", port, store.DATA)
    uvicorn.run(app, host=os.environ.get("E2_HOST", "127.0.0.1"), port=port, log_level="warning")


if __name__ == "__main__":
    run()
