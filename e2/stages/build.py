"""Stage 2 - Build & Dependencies: download dependencies and compile with the project's own tool."""
from pathlib import Path

from . import tech


async def run(ctx, key: str) -> dict:
    ws: Path = ctx.workspace
    cfg = ctx.config["build"]
    if not ws.exists() or not any(ws.iterdir()):
        raise RuntimeError("Workspace is empty. The Source stage must succeed first")
    tool = tech.pick(ws, cfg.get("technology"))
    cmd = (cfg.get("command") or "").strip()
    ctx.log(key, f"Technology: {tool or 'unknown'} · building version {ctx.run['version']}")
    if cmd:
        ctx.log(key, "Using the custom build command")
        await ctx.sh(key, *tech.shell(cmd), cwd=ws)
    elif cfg.get("technology") == "custom":
        raise ValueError("Technology is 'Custom command' but no build command is set")
    elif tool is None:
        raise RuntimeError("Could not detect the technology. Choose one in the Build options or set a custom command")
    else:
        await BUILDERS[tool](ctx, key, ws)
    outputs = collect(ws, tool)
    for o in outputs:
        ctx.log(key, f"output: {o.relative_to(ws)}")
    if not outputs:
        ctx.log(key, "No packaged outputs found (fine for Go / Flutter / Xcode: binaries are made per platform).")
    return {"summary": f"{tool or 'custom'} · {len(outputs)} output(s)",
            "detail": {"tool": tool or "custom", "outputs": [str(o.relative_to(ws)) for o in outputs]}}


async def _maven(ctx, key, ws):
    m = tech.mvn(ws)
    tech.require(m)
    await ctx.sh(key, m, "-B", "-ntp", "-DskipTests", "package", cwd=ws)


async def _gradle(ctx, key, ws):
    g = tech.gradle(ws)
    tech.require(g)
    await ctx.sh(key, g, "--no-daemon", "assemble", cwd=ws)


async def _npm(ctx, key, ws):
    pm = tech.node_pm(ws)
    tech.require(pm)
    if pm == "npm":
        await ctx.sh(key, "npm", "ci" if (ws / "package-lock.json").exists() else "install", "--no-audit", "--no-fund", cwd=ws)
    else:
        await ctx.sh(key, pm, "install", cwd=ws)
    if "build" in tech.scripts(ws):
        await ctx.sh(key, pm, "run", "build", cwd=ws)
    else:
        ctx.log(key, "package.json has no build script; dependencies installed only")


async def _python(ctx, key, ws):
    if tech.py_mode(ws) == "uv":
        await ctx.sh(key, "uv", "sync", cwd=ws)
    else:
        await ctx.sh(key, tech.PY, "-m", "venv", ".e2venv", cwd=ws)
        (ws / ".e2venv" / ".gitignore").write_text("*\n")  # keep E2's venv out of packages
        env = tech.py_env(ws)
        await ctx.sh(key, "python", "-m", "pip", "install", "-q", "--upgrade", "pip", cwd=ws, env=env)
        if (ws / "requirements.txt").exists():
            await ctx.sh(key, "python", "-m", "pip", "install", "-r", "requirements.txt", cwd=ws, env=env)
        if (ws / "pyproject.toml").exists() or (ws / "setup.py").exists():
            await ctx.sh(key, "python", "-m", "pip", "install", ".", cwd=ws, env=env)
    if (ws / "pyproject.toml").exists() or (ws / "setup.py").exists():
        if tech.has("uv"):
            await ctx.sh(key, "uv", "build", "--wheel", "--out-dir", "dist", cwd=ws)
        else:
            await ctx.sh(key, "python", "-m", "pip", "wheel", ".", "--no-deps", "-w", "dist", cwd=ws, env=tech.py_env(ws))


async def _go(ctx, key, ws):
    tech.require("go")
    await ctx.sh(key, "go", "mod", "download", cwd=ws)
    await ctx.sh(key, "go", "build", "./...", cwd=ws)


async def _rust(ctx, key, ws):
    tech.require("cargo")
    await ctx.sh(key, "cargo", "fetch", cwd=ws)
    await ctx.sh(key, "cargo", "build", "--release", cwd=ws)


async def _dotnet(ctx, key, ws):
    tech.require("dotnet")
    await ctx.sh(key, "dotnet", "restore", cwd=ws)
    await ctx.sh(key, "dotnet", "build", "-c", "Release", "--no-restore", cwd=ws)


async def _flutter(ctx, key, ws):
    tech.require("flutter")
    await ctx.sh(key, "flutter", "pub", "get", cwd=ws)


async def _xcode(ctx, key, ws):
    tech.require("xcodebuild")
    await ctx.sh(key, "xcodebuild", "-list", cwd=ws)
    ctx.log(key, "Xcode apps are compiled per platform in the Target Platform stage")


async def _make(ctx, key, ws):
    tech.require("make")
    await ctx.sh(key, "make", cwd=ws)


BUILDERS = {"maven": _maven, "gradle": _gradle, "npm": _npm, "python": _python, "go": _go, "rust": _rust,
            "dotnet": _dotnet, "flutter": _flutter, "xcode": _xcode, "make": _make}


def collect(ws: Path, tool: str | None) -> list[Path]:
    """Files/folders the build produced that are worth packaging."""
    out: list[Path] = []
    if tool == "maven":
        out = [p for p in ws.glob("**/target/*.[jw]ar") if not p.name.startswith("original-")
               and not p.stem.endswith(("-sources", "-javadoc"))]
    elif tool == "gradle":
        out = list(ws.glob("**/build/libs/*.jar")) + list(ws.glob("**/build/distributions/*"))
        out = [p for p in out if not p.stem.endswith(("-plain", "-sources", "-javadoc"))]
    elif tool == "npm":
        out = [ws / d for d in ("dist", "build", "out") if (ws / d).is_dir()]
    elif tool == "python":
        out = list((ws / "dist").glob("*.whl")) + list((ws / "dist").glob("*.tar.gz"))
    elif tool == "rust":
        rel = ws / "target" / "release"
        out = [p for p in rel.glob("*") if p.is_file() and p.stat().st_mode & 0o111 and not p.suffix] if rel.exists() else []
    elif tool == "dotnet":
        out = [p for p in ws.glob("**/bin/Release/*") if p.is_dir()]
    elif tool in (None, "make", "custom"):
        out = [ws / d for d in ("dist", "build", "out") if (ws / d).is_dir()]
    return sorted(set(out))
