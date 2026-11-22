"""File-based storage. Everything lives under data/ (override with E2_DATA):

data/projects/<pid>/project.json           project config, current version, run counter
data/projects/<pid>/workspace/             checked-out / copied source (stage 1 output)
data/projects/<pid>/runs/<n>/run.json      run state: overall + per-stage status (used to resume)
data/projects/<pid>/runs/<n>/events.jsonl  every state transition, append-only audit trail
data/projects/<pid>/runs/<n>/logs/<stage>.log   full log of each stage
"""
import copy
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("E2_DATA", ROOT / "data")).resolve()
PROJ = DATA / "projects"
_lock = threading.RLock()

STAGES = [
    ("source", "Source"),
    ("build", "Build & Dependencies"),
    ("platform", "Target Platform"),
    ("test", "Test"),
    ("verify", "Verify Artifact"),
    ("publish", "Publish"),
]
STAGE_KEYS = [k for k, _ in STAGES]

DEFAULT_CONFIG = {
    "source": {"type": "git", "url": "", "branch": "main", "commit": "", "local_path": "",
               "trigger": "manual", "poll_seconds": 60},
    "build": {"technology": "auto", "command": ""},
    "platform": {"mac": True, "windows": False, "linux": False, "ios": False, "android": False,
                 "readme": True, "readme_path": "README.md", "release_notes": True, "release_notes_path": "RELEASE_NOTES.md",
                 "icon": False, "icon_path": "icon.png",
                 "installer": False, "app_name": "", "app_url": "", "app_env": ""},
    "test": {"unit": True, "integration": False, "regression": False, "docker": False,
             "security": False, "license": False},
    "verify": {"checksum": True, "signature": False, "smoke": False},
    "publish": {"destination": "local", "target": ""},
}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, p)  # atomic: a crash never leaves a half-written state file


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ── projects ──
def pdir(pid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,48}", pid or ""):
        raise KeyError(pid)
    return PROJ / pid


def workspace(pid: str) -> Path:
    return pdir(pid) / "workspace"


def project(pid: str) -> dict | None:
    try:
        p = _read(pdir(pid) / "project.json")
    except KeyError:
        return None
    if p:  # options added in newer E2 versions get their defaults
        p["config"] = merge(DEFAULT_CONFIG, p.get("config") or {})
    return p


def projects() -> list[dict]:
    if not PROJ.exists():
        return []
    out = [p for d in PROJ.iterdir() if (p := _read(d / "project.json"))]
    return sorted(out, key=lambda p: p["created"])


def create_project(name: str, config: dict, version: str) -> dict:
    with _lock:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "project"
        pid, i = base, 2
        while (PROJ / pid).exists():
            pid, i = f"{base}-{i}", i + 1
        p = {"id": pid, "name": name, "version": version, "run_seq": 0, "created": now(), "updated": now(),
             "config": merge(DEFAULT_CONFIG, config), "last_fingerprint": None, "last_source": None}
        _write(pdir(pid) / "project.json", p)
        return p


def update_project(pid: str, **fields) -> dict:
    with _lock:
        p = project(pid)
        if p is None:
            raise KeyError(pid)
        if "config" in fields:
            fields["config"] = merge(p["config"], fields["config"])
        p.update(fields, updated=now())
        _write(pdir(pid) / "project.json", p)
        return p


def delete_project(pid: str) -> None:
    shutil.rmtree(pdir(pid), ignore_errors=True)


# ── versions ──
def next_version(current: str, bump: str, custom: str = "") -> str:
    if bump == "custom":
        if not VERSION_RE.match(custom or ""):
            raise ValueError("Version must look like MAJOR.MINOR.PATCH, e.g. 1.4.0")
        return custom
    ma, mi, pa = (int(x) for x in current.split("."))
    return {"major": f"{ma + 1}.0.0", "minor": f"{ma}.{mi + 1}.0", "patch": f"{ma}.{mi}.{pa + 1}",
            "none": current}[bump]


# ── runs ──
def rdir(pid: str, rid: str) -> Path:
    if not str(rid).isdigit():
        raise KeyError(rid)
    return pdir(pid) / "runs" / str(rid)


def run(pid: str, rid: str) -> dict | None:
    try:
        return _read(rdir(pid, rid) / "run.json")
    except KeyError:
        return None


def runs(pid: str, limit: int = 50) -> list[dict]:
    d = pdir(pid) / "runs"
    if not d.exists():
        return []
    ids = sorted((int(x.name) for x in d.iterdir() if x.name.isdigit()), reverse=True)[:limit]
    return [r for i in ids if (r := run(pid, str(i)))]


def create_run(pid: str, trigger: str, bump: str = "patch", custom: str = "") -> dict:
    with _lock:
        p = project(pid)
        if p is None:
            raise KeyError(pid)
        version = next_version(p["version"], bump, custom)
        n = p["run_seq"] + 1
        r = {"id": str(n), "number": n, "project": pid, "version": version, "trigger": trigger,
             "status": "queued", "created": now(), "started": None, "finished": None,
             "config": p["config"],  # snapshot: a run always uses the config it started with
             "stages": {k: {"status": "pending", "started": None, "finished": None, "summary": "", "detail": {}}
                        for k in STAGE_KEYS}}
        _write(rdir(pid, str(n)) / "run.json", r)
        update_project(pid, run_seq=n, version=version)
        event(pid, str(n), "run", "created", trigger=trigger, version=version)
        return r


def save_run(r: dict) -> None:
    with _lock:
        _write(rdir(r["project"], r["id"]) / "run.json", r)


def event(pid: str, rid: str, stage: str, state: str, **extra) -> None:
    line = json.dumps({"t": now(), "stage": stage, "state": state, **extra})
    with _lock, open(rdir(pid, rid) / "events.jsonl", "a") as f:
        f.write(line + "\n")


def log(pid: str, rid: str, stage: str, text: str) -> None:
    p = rdir(pid, rid) / "logs" / f"{stage}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock, open(p, "a") as f:
        for ln in str(text).splitlines() or [""]:
            f.write(f"[{ts}] {ln}\n")


def read_log(pid: str, rid: str, stage: str, offset: int = 0) -> tuple[str, int]:
    if stage not in STAGE_KEYS:
        raise KeyError(stage)
    p = rdir(pid, rid) / "logs" / f"{stage}.log"
    if not p.exists():
        return "", 0
    with open(p, "rb") as f:
        f.seek(offset)
        data = f.read(512_000)
    return data.decode("utf-8", "replace"), offset + len(data)


def mark_interrupted() -> int:
    """On startup: any run still 'running' died with the server. Mark it so it can be resumed."""
    n = 0
    for p in projects():
        for r in runs(p["id"], limit=10):
            if r["status"] in ("running", "queued"):
                for st in r["stages"].values():
                    if st["status"] == "running":
                        st.update(status="interrupted", finished=now())
                r.update(status="interrupted", finished=now())
                save_run(r)
                event(p["id"], r["id"], "run", "interrupted")
                n += 1
    return n
