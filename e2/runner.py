"""Runs a pipeline stage by stage, persisting state after every transition so it can be resumed."""
import asyncio
import logging
import os
import re
import time

from . import store
from .stages import source

log = logging.getLogger("e2.runner")
PROGRESS = re.compile(r"\d+% \(\d+/\d+\)")  # git progress ticks: keep only the final "done" line
HANDLERS = {"source": source.run}  # stages not listed here are skipped as "not implemented yet"
TASKS: dict[tuple[str, str], asyncio.Task] = {}


class Ctx:
    def __init__(self, r: dict):
        self.run, self.pid, self.rid, self.config = r, r["project"], r["id"], r["config"]
        self.workspace = store.workspace(self.pid)

    def log(self, stage: str, text: str) -> None:
        store.log(self.pid, self.rid, stage, text)

    async def sh(self, stage: str, *args, cwd=None, check=True, quiet=False) -> str:
        """Run a command, streaming its output into the stage log."""
        if not quiet:
            self.log(stage, "$ " + " ".join(str(a) for a in args))
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        proc = await asyncio.create_subprocess_exec(*map(str, args), cwd=cwd, env=env,
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
        except asyncio.CancelledError:
            proc.kill()
            raise
        if check and rc != 0:
            raise RuntimeError(f"`{args[0]} {args[1] if len(args) > 1 else ''}` exited with code {rc}")
        return "\n".join(out)


def active(pid: str) -> bool:
    return any(k[0] == pid and not t.done() for k, t in TASKS.items())


def start(pid: str, rid: str, resume: bool = False) -> None:
    if (pid, rid) in TASKS and not TASKS[(pid, rid)].done():
        raise RuntimeError("Run is already in progress")
    TASKS[(pid, rid)] = asyncio.create_task(_run(pid, rid, resume))


def cancel(pid: str, rid: str) -> bool:
    t = TASKS.get((pid, rid))
    if t and not t.done():
        t.cancel()
        return True
    return False


async def _run(pid: str, rid: str, resume: bool) -> None:
    r = store.run(pid, rid)
    ctx = Ctx(r)
    r.update(status="running", finished=None)
    r["started"] = r["started"] or store.now()
    store.save_run(r)
    store.event(pid, rid, "run", "resumed" if resume else "started")
    current = None
    try:
        for key in store.STAGE_KEYS:
            st = r["stages"][key]
            if st["status"] in ("success", "skipped"):
                continue  # already done in an earlier attempt: resume picks up after it
            current = key
            if resume and st["status"] != "pending":
                ctx.log(key, f"── resuming (previous attempt: {st['status']}) ──")
            st.update(status="running", started=store.now(), finished=None, summary="")
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
                st.update(status="success", finished=store.now(), summary=res.get("summary", ""),
                          detail=res.get("detail", {}))
                ctx.log(key, f"✓ stage finished in {time.monotonic() - t0:.1f}s")
            store.save_run(r)
            store.event(pid, rid, key, st["status"])
            current = None
        r.update(status="success", finished=store.now())
        store.event(pid, rid, "run", "success")
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
            try:
                fp = await source.fingerprint(s)
            except Exception as e:  # noqa: BLE001
                log.warning("poll %s failed: %s", p["id"], e)
                continue
            if fp and fp != p.get("last_fingerprint"):
                log.info("change detected in %s, starting a run", p["id"])
                r = store.create_run(p["id"], trigger="auto", bump="patch")
                start(p["id"], r["id"])
