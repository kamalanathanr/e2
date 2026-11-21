"""E2 - FastAPI app: JSON API + single-page dashboard."""
import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
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
                                        "attention": sum(s in ("interrupted", "cancelled") for s in st),
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
    return {"project": _project(pid), "runs": store.runs(pid, limit=50), "active": runner.active(pid)}


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


@app.post("/api/projects/{pid}/runs/{rid}/resume")
async def resume_run(pid: str, rid: str):
    r = _run(pid, rid)
    if runner.active(pid):
        raise HTTPException(409, "A run is already in progress for this project")
    if r["status"] not in ("failed", "cancelled", "interrupted"):
        raise HTTPException(400, f"Run is {r['status']}; only failed, cancelled or interrupted runs can resume")
    runner.start(pid, rid, resume=True)
    return {"ok": True}


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
