"""Shared helpers: which technology a workspace uses, and how to invoke its tools."""
import json
import os
import platform as _platform
import shutil
from pathlib import Path

DETECT_ORDER = [
    ("maven", "pom.xml"), ("gradle", "build.gradle"), ("gradle", "build.gradle.kts"),
    ("flutter", "pubspec.yaml"), ("npm", "package.json"), ("python", "pyproject.toml"),
    ("python", "requirements.txt"), ("python", "setup.py"), ("go", "go.mod"), ("rust", "Cargo.toml"),
    ("dotnet", "*.sln"), ("dotnet", "*.csproj"), ("xcode", "*.xcodeproj"), ("make", "Makefile"),
]
SCAN_SKIP = {".git", "node_modules", ".e2venv", ".venv", "venv", "target", "build", "dist", ".gradle",
             "__pycache__", ".dart_tool", ".e2build", "Pods", ".idea", ".next", "bin", "obj"}
HOST = {"Darwin": "mac", "Windows": "windows", "Linux": "linux"}.get(_platform.system(), "linux")
WIN = HOST == "windows"
VENV_BIN = "Scripts" if WIN else "bin"  # where a venv keeps python / pip
PY = "python" if WIN else "python3"  # Windows installs python.exe (and the py launcher), not python3
ARCH = "arm64" if _platform.machine().lower() in ("arm64", "aarch64") else "amd64"


def pick(ws: Path, configured: str | None) -> str | None:
    if configured and configured not in ("auto", "custom"):
        return configured
    for tool, pat in DETECT_ORDER:
        if any(ws.glob(pat)):
            return tool
    return None


def has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def shell(cmd: str) -> list[str]:
    """argv that runs a one-line shell command on this OS."""
    return ["cmd", "/d", "/s", "/c", cmd] if WIN else ["/bin/sh", "-c", cmd]


def site_packages(venv: Path) -> list[Path]:
    return list(venv.glob("Lib/site-packages")) + list(venv.glob("lib/python*/site-packages"))


def require(*cmds: str) -> None:
    missing = [c for c in cmds if not c.startswith("./") and not has(c)]
    if missing:
        raise RuntimeError(f"Not installed on this machine: {', '.join(missing)}")


def wrapper(ws: Path, name: str, fallback: str) -> str:
    """Prefer the project's own wrapper (mvnw / gradlew) when it ships one."""
    if WIN:  # Windows wrappers are mvnw.cmd / gradlew.bat
        for ext in (".cmd", ".bat"):
            if (ws / (name + ext)).exists():
                return str(ws / (name + ext))
        return fallback
    w = ws / name
    if w.exists():
        w.chmod(w.stat().st_mode | 0o111)
        return f"./{name}"
    return fallback


def mvn(ws): return wrapper(ws, "mvnw", "mvn")
def gradle(ws): return wrapper(ws, "gradlew", "gradle")


def node_pm(ws: Path) -> str:
    if (ws / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (ws / "yarn.lock").exists():
        return "yarn"
    return "npm"


def scripts(ws: Path) -> dict:
    try:
        return json.loads((ws / "package.json").read_text()).get("scripts", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def py_mode(ws: Path) -> str:
    return "uv" if (ws / "uv.lock").exists() and has("uv") else "venv"


def py_env(ws: Path) -> dict:
    """Environment that puts the project's private venv first on PATH."""
    venv = ws / (".venv" if py_mode(ws) == "uv" else ".e2venv")
    return {"VIRTUAL_ENV": str(venv), "PATH": f"{venv / VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}"}


def is_android(ws: Path) -> bool:
    for f in list(ws.glob("*.gradle*")) + list(ws.glob("*/build.gradle*")):
        try:
            if "com.android" in f.read_text(errors="ignore"):
                return True
        except OSError:
            pass
    return False


def walk_files(ws: Path, max_size: int = 1_000_000):
    """Source files only (skips VCS, dependencies and build output)."""
    for d, dirs, files in os.walk(ws):
        dirs[:] = [x for x in dirs if x not in SCAN_SKIP]
        for f in files:
            p = Path(d) / f
            try:
                if p.stat().st_size <= max_size:
                    yield p
            except OSError:
                pass
