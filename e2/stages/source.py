"""Stage 1 - Source: check out a Git branch (optionally a commit/tag) or copy a local folder
into the project workspace, then detect which technology the project uses."""
import asyncio
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .. import store

SKIP_DIRS = {"node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__", ".gradle", ".idea", ".next"}
TECH = [
    ("pom.xml", "Maven (Java)"), ("build.gradle", "Gradle"), ("build.gradle.kts", "Gradle (Kotlin)"),
    ("package.json", "Node.js"), ("pyproject.toml", "Python"), ("requirements.txt", "Python (pip)"),
    ("go.mod", "Go"), ("Cargo.toml", "Rust"), ("*.sln", ".NET"), ("*.csproj", ".NET"),
    ("pubspec.yaml", "Flutter / Dart"), ("CMakeLists.txt", "CMake (C/C++)"), ("Makefile", "Make"),
    ("Dockerfile", "Docker"), ("*.xcodeproj", "Xcode"),
]


def detect(ws: Path) -> list[str]:
    found = []
    for pat, name in TECH:
        if any(ws.glob(pat)) and name not in found:
            found.append(name)
    return found


async def run(ctx, key: str) -> dict:
    s = ctx.config["source"]
    ws: Path = ctx.workspace
    if s["type"] == "git":
        detail = await _git(ctx, key, s, ws)
    else:
        detail = await asyncio.to_thread(_local, ctx, key, s, ws)
    detail["technologies"] = detect(ws)
    detail["files"] = sum(len(f) for d, _, f in os.walk(ws) if "/.git" not in d)
    ctx.log(key, f"Detected technology: {', '.join(detail['technologies']) or 'unknown'}")
    ctx.log(key, f"Workspace: {ws} ({detail['files']} files)")
    store.update_project(ctx.pid, last_fingerprint=detail.get("fingerprint"), last_source=detail)
    where = f"{detail.get('branch', '')}@{detail['commit'][:8]}" if detail.get("commit") else s.get("local_path", "")
    return {"summary": f"{where} · {', '.join(detail['technologies']) or 'technology unknown'}", "detail": detail}


async def _git(ctx, key, s, ws: Path) -> dict:
    url, branch, pin = s.get("url", "").strip(), s.get("branch", "").strip(), s.get("commit", "").strip()
    if not url:
        raise ValueError("No repository URL configured")
    if not branch:
        raise ValueError("No branch configured")
    if (ws / ".git").exists():
        ctx.log(key, "Existing checkout found, updating it")
        await ctx.sh(key, "git", "remote", "set-url", "origin", url, cwd=ws)
        await ctx.sh(key, "git", "fetch", "--prune", "--tags", "--progress", "origin",
                     f"+refs/heads/{branch}:refs/remotes/origin/{branch}", cwd=ws)
        await ctx.sh(key, "git", "checkout", "-f", "-B", branch, f"origin/{branch}", cwd=ws)
        await ctx.sh(key, "git", "reset", "--hard", f"origin/{branch}", cwd=ws)
        await ctx.sh(key, "git", "clean", "-fdx", cwd=ws)
    else:
        if ws.exists():
            shutil.rmtree(ws)
        ws.parent.mkdir(parents=True, exist_ok=True)
        await ctx.sh(key, "git", "clone", "--progress", "--branch", branch, url, ws)
    branch_tip = (await ctx.sh(key, "git", "rev-parse", "HEAD", cwd=ws, quiet=True)).strip()
    if pin:
        ctx.log(key, f"Pinning to {pin}")
        await ctx.sh(key, "git", "checkout", "-f", "--detach", pin, cwd=ws)
    sha = (await ctx.sh(key, "git", "rev-parse", "HEAD", cwd=ws, quiet=True)).strip()
    info = (await ctx.sh(key, "git", "log", "-1", "--format=%an%x1f%aI%x1f%s", cwd=ws, quiet=True)).split("\x1f")
    ctx.log(key, f"HEAD {sha[:10]}  {info[-1] if info else ''}")
    return {"kind": "git", "url": url, "branch": branch, "pinned": pin or None, "commit": sha,
            "author": info[0] if len(info) > 2 else "", "date": info[1] if len(info) > 2 else "",
            "message": info[2] if len(info) > 2 else "", "fingerprint": branch_tip}


def _local(ctx, key, s, ws: Path) -> dict:
    src = Path(os.path.expanduser(s.get("local_path", "").strip())).resolve()
    if not s.get("local_path", "").strip() or not src.is_dir():
        raise ValueError(f"Local folder not found: {src}")

    def ignore(d, names):
        skip = {n for n in names if n in SKIP_DIRS}
        if Path(d).resolve() / "data" == store.DATA:  # never copy our own data dir into itself
            skip.add("data")
        return skip

    ctx.log(key, f"Copying {src} → {ws}")
    ctx.log(key, f"Skipping: {', '.join(sorted(SKIP_DIRS))}")
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(src, ws, ignore=ignore, symlinks=True)
    detail = {"kind": "local", "path": str(src), "fingerprint": _local_fp(src)}
    if (src / ".git").exists():  # a local git repo: record where it is
        try:
            git = lambda *a: subprocess.run(["git", "-C", str(src), *a], capture_output=True, text=True, timeout=10).stdout.strip()  # noqa: E731
            detail.update(branch=git("rev-parse", "--abbrev-ref", "HEAD"), commit=git("rev-parse", "HEAD"),
                          message=git("log", "-1", "--format=%s"))
            ctx.log(key, f"Local git repo on {detail['branch']} @ {detail['commit'][:10]}")
        except Exception:  # noqa: BLE001
            pass
    return detail


def _local_fp(src: Path) -> str:
    h = hashlib.sha1()
    for d, dirs, files in os.walk(src):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and x != ".git")
        for f in sorted(files):
            try:
                st = os.stat(os.path.join(d, f))
                h.update(f"{os.path.relpath(os.path.join(d, f), src)}:{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                pass
    return h.hexdigest()


async def fingerprint(s: dict) -> str | None:
    """Cheap 'has the source changed?' check used by the auto trigger."""
    if s["type"] == "git":
        if not s.get("url") or not s.get("branch"):
            return None
        out = await remote_heads(s["url"], s["branch"])
        return out.get(s["branch"])
    p = Path(os.path.expanduser(s.get("local_path", ""))).resolve()
    return await asyncio.to_thread(_local_fp, p) if p.is_dir() else None


async def remote_heads(url: str, branch: str | None = None) -> dict[str, str]:
    args = ["git", "ls-remote", "--heads", url] + ([f"refs/heads/{branch}"] if branch else [])
    proc = await asyncio.create_subprocess_exec(*args, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 30)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("git ls-remote timed out")
    if proc.returncode != 0:
        raise RuntimeError(err.decode().strip() or "git ls-remote failed")
    heads = {}
    for ln in out.decode().splitlines():
        sha, _, ref = ln.partition("\t")
        heads[ref.removeprefix("refs/heads/")] = sha
    return heads
