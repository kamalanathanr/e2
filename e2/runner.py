"""Runs a pipeline stage by stage, persisting state after every transition so it can be resumed."""
import asyncio
import logging
import os
import re
import shutil
import time

from . import store
from .stages import build, platform, publish, source, test, verify

log = logging.getLogger("e2.runner")
PROGRESS = re.compile(r"\d+% \(\d+/\d+\)")  # git progress ticks: keep only the final "done" line
HANDLERS = {"source": source.run, "build": build.run, "platform": platform.run, "test": test.run,
            "verify": verify.run, "publish": publish.run}
TASKS: dict[tuple[str, str], asyncio.Task] = {}


class Ctx:
    def __init__(self, r: dict):
        self.run, self.pid, self.rid, self.config = r, r["project"], r["id"], r["config"]
        self.workspace = store.workspace(self.pid)
        self.artifacts = store.rdir(self.pid, self.rid) / "artifacts"
        self.last_rc = 0

    def log(self, stage: str, text: str) -> None:
        store.log(self.pid, self.rid, stage, text)

    async def sh(self, stage: str, *args, cwd=None, check=True, quiet=False, env=None) -> str:
        """Run a command, streaming its output into the stage log."""
        if not quiet:
            self.log(stage, "$ " + " ".join(str(a) for a in args))
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "CI": "true", "E2_VERSION": self.run["version"],
               "E2_BUILD_NUMBER": str(self.run["number"]), **(env or {})}
        argv = [str(a) for a in args]
        if os.name == "nt":  # CreateProcess ignores the child's PATH and won't find npm.cmd / gh.exe by bare name
            argv[0] = shutil.which(argv[0], path=env.get("PATH")) or argv[0]
        proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, buf = [], b""
        try:
            while chunk := await proc.stdout.read(4096):
                buf += chunk.replace(b"\r", b"\n")
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    s = ln.decode("utf-8", "replace").rstrip()
                    if s and not (PROGRESS.search(s) and "done" not in s):
                        out.append(s)
                        if not quiet:
                            self.log(stage, s)
            if buf.strip():
                out.append(buf.decode("utf-8", "replace").rstrip())
                if not quiet:
                    self.log(stage, out[-1])
            rc = await proc.wait()
            self.last_rc = rc
        except asyncio.CancelledError:
            proc.kill()
            raise
        if check and rc != 0:
            raise RuntimeError(f"`{args[0]} {args[1] if len(args) > 1 else ''}` exited with code {rc}")
        return "\n".join(out)


def active(pid: str) -> bool:
    return any(k[0] == pid and not t.done() for k, t in TASKS.items())


def start(pid: str, rid: str, resume: bool = False, only: str | None = None) -> None:
    if (pid, rid) in TASKS and not TASKS[(pid, rid)].done():
        raise RuntimeError("Run is already in progress")
    TASKS[(pid, rid)] = asyncio.create_task(_run(pid, rid, resume, only))


def cancel(pid: str, rid: str) -> bool:
    t = TASKS.get((pid, rid))
    if t and not t.done():
        t.cancel()
        return True
    return False


async def _run(pid: str, rid: str, resume: bool, only: str | None = None) -> None:
    r = store.run(pid, rid)
    ctx = Ctx(r)
    r.update(status="running", finished=None)
    r["started"] = r["started"] or store.now()
    store.save_run(r)
    store.event(pid, rid, "run", f"stage {only} only" if only else "resumed" if resume else "started")
    current = None
    try:
        for key in store.STAGE_KEYS:
            st = r["stages"][key]
            if only and key != only:
                continue
            if st["status"] in ("success", "skipped"):
                continue  # already done in an earlier attempt: resume picks up after it
            current = key
            if resume and st["status"] != "pending":
                ctx.log(key, f"── resuming (previous attempt: {st['status']}) ──")
            st.update(status="running", started=store.now(), finished=None, summary="", detail={})
            store.save_run(r)
            store.event(pid, rid, key, "running")
            t0 = time.monotonic()
            handler = HANDLERS.get(key)
            if handler is None:
                ctx.log(key, "This stage is not implemented yet. Its options are saved and will apply once it is built.")
                st.update(status="skipped", finished=store.now(), summary="Not implemented yet")
            else:
                try:
                    res = await handler(ctx, key) or {}
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - any failure fails the stage
                    ctx.log(key, f"ERROR: {e}")
                    st.update(status="failed", finished=store.now(), summary=str(e)[:300])
                    r.update(status="failed", finished=store.now())
                    store.save_run(r)
                    store.event(pid, rid, key, "failed", error=str(e)[:500])
                    return
                st.update(status=res.get("status", "success"), finished=store.now(), summary=res.get("summary", ""),
                          detail=res.get("detail", {}))
                ctx.log(key, f"✓ stage finished in {time.monotonic() - t0:.1f}s")
            store.save_run(r)
            store.event(pid, rid, key, st["status"])
            current = None
        left = [k for k in store.STAGE_KEYS if r["stages"][k]["status"] not in ("success", "skipped")]
        r.update(status="incomplete" if left else "success", finished=store.now())
        store.event(pid, rid, "run", r["status"])
    except asyncio.CancelledError:
        if current:
            r["stages"][current].update(status="cancelled", finished=store.now())
            ctx.log(current, "Cancelled by user.")
        r.update(status="cancelled", finished=store.now())
        store.event(pid, rid, "run", "cancelled")
    finally:
        store.save_run(r)


# ── auto trigger: watch the source and start a run when it changes ──
_last_poll: dict[str, float] = {}
watch: dict[str, dict] = {}  # pid -> {checked, every, result, error, changed, rid}


def watch_info(pid: str) -> dict | None:
    p = store.project(pid)
    if not p or p["config"]["source"].get("trigger") != "auto":
        return None
    w = dict(watch.get(pid, {}))
    w["every"] = max(15, int(p["config"]["source"].get("poll_seconds") or 60))
    w["paused"] = active(pid)
    return w


async def poller() -> None:
    while True:
        await asyncio.sleep(10)
        for p in store.projects():
            s = p["config"]["source"]
            if s.get("trigger") != "auto" or active(p["id"]):
                continue
            if time.time() - _last_poll.get(p["id"], 0) < max(15, int(s.get("poll_seconds") or 60)):
                continue
            _last_poll[p["id"]] = time.time()
            w = watch.setdefault(p["id"], {})
            w["checked"] = store.now()
            try:
                fp = await source.fingerprint(s)
            except Exception as e:  # noqa: BLE001
                log.warning("poll %s failed: %s", p["id"], e)
                w.update(result="error", error=str(e)[:200])
                continue
            if fp and fp != p.get("last_fingerprint"):
                log.info("change detected in %s, starting a run", p["id"])
                r = store.create_run(p["id"], trigger="auto", bump="patch")
                w.update(result="changed", error="", changed=store.now(), rid=r["id"], number=r.get("number"),
                         commit=str(fp)[:12])
                start(p["id"], r["id"])
            else:
                w.update(result="no change", error="")
