"""Dependency + OS-specific code analysis for the Build & Dependencies page."""
import json
import os
import re
from pathlib import Path

from . import tech

PLAT_WORDS = {"windows": ("win32", "windows", "win-", "-win", "msvc"), "mac": ("darwin", "macos", "macosx", "osx", "apple"),
              "linux": ("linux", "manylinux", "musl", "gnu"), "android": ("android",), "ios": ("ios", "iphone")}


def _words(text: str) -> list[str]:
    t = text.lower()
    return [p for p, ws in PLAT_WORDS.items() if any(w in t for w in ws)]


def _dir_size(p: Path) -> int:
    total = 0
    for d, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(d) / f).lstat().st_size
            except OSError:
                pass
    return total


def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def _python(ws: Path) -> list[dict]:
    declared: set[str] = set()
    req = ws / "requirements.txt"
    if req.exists():
        for line in req.read_text(errors="ignore").splitlines():
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if m and not line.strip().startswith(("#", "-")):
                declared.add(_norm(m.group(1)))
    pp = ws / "pyproject.toml"
    if pp.exists():
        txt = pp.read_text(errors="ignore")
        for blk in re.findall(r"dependencies\s*=\s*\[(.*?)\]", txt, re.S):
            for m in re.findall(r"[\"']([A-Za-z0-9][A-Za-z0-9._-]*)", blk):
                declared.add(_norm(m))
    out = []
    sps = tech.site_packages(ws / ".e2venv") + tech.site_packages(ws / ".venv")
    for sp in sps[:1]:
        for di in sp.glob("*.dist-info"):
            name, _, ver = di.name[:-10].rpartition("-")
            size = 0
            rec = di / "RECORD"
            if rec.exists():
                for line in rec.read_text(errors="ignore").splitlines():
                    f = sp / line.split(",")[0]
                    try:
                        size += f.stat().st_size
                    except OSError:
                        pass
            plats: list[str] = []
            w = di / "WHEEL"
            if w.exists():
                tags = " ".join(re.findall(r"Tag:\s*(\S+)", w.read_text(errors="ignore")))
                if not tags.endswith("-any") and "-any " not in tags + " ":
                    plats = _words(tags) or ["native"]
            out.append({"name": name, "version": ver, "size": size, "direct": _norm(name) in declared,
                        "platforms": plats, "note": "native wheel" if plats else ""})
    if not out:
        out = [{"name": n, "version": "", "size": None, "direct": True, "platforms": [], "note": "not installed yet"} for n in sorted(declared)]
    return out


def _npm(ws: Path) -> list[dict]:
    try:
        pj = json.loads((ws / "package.json").read_text())
    except Exception:  # noqa: BLE001
        pj = {}
    direct = set((pj.get("dependencies") or {})) | set((pj.get("devDependencies") or {}))
    nm = ws / "node_modules"
    out = []
    if nm.is_dir():
        pkgs = []
        for d in nm.iterdir():
            if d.name.startswith("@"):
                pkgs += [x for x in d.iterdir() if x.is_dir()]
            elif d.is_dir() and not d.name.startswith("."):
                pkgs.append(d)
        for d in pkgs:
            name = f"{d.parent.name}/{d.name}" if d.parent.name.startswith("@") else d.name
            ver, plats = "", []
            try:
                m = json.loads((d / "package.json").read_text())
                ver = m.get("version", "")
                plats = sorted({p for o in (m.get("os") or []) for p in _words(o if isinstance(o, str) else "")}) or _words(name)
                if (m.get("os") or m.get("cpu")) and not plats:
                    plats = ["native"]
            except Exception:  # noqa: BLE001
                plats = _words(name)
            out.append({"name": name, "version": ver, "size": _dir_size(d), "direct": name in direct, "platforms": plats,
                        "note": "native binary" if plats else ""})
    else:
        out = [{"name": n, "version": "", "size": None, "direct": True, "platforms": _words(n), "note": "not installed yet"} for n in sorted(direct)]
    return out


def _maven(ws: Path) -> list[dict]:
    out, seen = [], set()
    repo = Path.home() / ".m2" / "repository"
    for pom in ws.glob("**/pom.xml"):
        if any(x in pom.parts for x in tech.SCAN_SKIP):
            continue
        for g, a in re.findall(r"<dependency>.*?<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>", pom.read_text(errors="ignore"), re.S):
            if (g, a) in seen:
                continue
            seen.add((g, a))
            d = repo / g.replace(".", "/") / a
            out.append({"name": f"{g}:{a}", "version": "", "size": _dir_size(d) if d.is_dir() else None, "direct": True,
                        "platforms": _words(a), "note": ""})
    return out


def _declared_only(ws: Path, tool: str) -> list[dict]:
    names: set[str] = set()
    if tool == "go" and (ws / "go.mod").exists():
        names = set(re.findall(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+v[\d.]", (ws / "go.mod").read_text(errors="ignore"), re.M))
    elif tool == "rust" and (ws / "Cargo.toml").exists():
        sec = re.search(r"\[dependencies\](.*?)(\n\[|\Z)", (ws / "Cargo.toml").read_text(errors="ignore"), re.S)
        names = set(re.findall(r"^([\w-]+)\s*=", sec.group(1), re.M)) if sec else set()
    elif tool == "flutter" and (ws / "pubspec.yaml").exists():
        sec = re.search(r"^dependencies:\n((?:[ \t]+.*\n?)+)", (ws / "pubspec.yaml").read_text(errors="ignore"), re.M)
        names = set(re.findall(r"^\s{2}([\w]+):", sec.group(1), re.M)) if sec else set()
    elif tool == "gradle":
        for f in ws.glob("**/build.gradle*"):
            names |= set(re.findall(r"""(?:implementation|api|compileOnly|runtimeOnly)\s*\(?\s*["']([^"':]+:[^"':]+)""", f.read_text(errors="ignore")))
    return [{"name": n, "version": "", "size": None, "direct": True, "platforms": _words(n), "note": "size not available for this tool"} for n in sorted(names)]


def dependencies(ws: Path, tool: str | None) -> list[dict]:
    if tool == "python":
        deps = _python(ws)
    elif tool == "npm":
        deps = _npm(ws)
    elif tool == "maven":
        deps = _maven(ws)
    elif tool:
        deps = _declared_only(ws, tool)
    else:
        deps = []
    deps.sort(key=lambda d: (d["size"] is None, -(d["size"] or 0), d["name"]))
    return deps


# ── OS-specific code scan ──
RULES = {
    "windows": r"\b(winreg|msvcrt|win32api|win32con|win32gui|ctypes\.windll|os\.startfile)\b|windows\.h|System\.Windows|\b_WIN32\b|Platform\.isWindows|C:\\\\|\.(exe|dll|bat|cmd)\b",
    "mac": r"\b(AppKit|Cocoa|osascript|launchctl|NSWorkspace|NSApplication)\b|/Library/|/Applications/|~/Library|Platform\.isMacOS|__APPLE__|\.dylib\b|\.plist\b",
    "linux": r"/proc/|/sys/class|systemctl|systemd|apt-get|\bepoll\b|\binotify\b|Platform\.isLinux|__linux__|\.so\b|/etc/\w+",
    "ios": r"\bUIKit\b|\bSwiftUI\b|TARGET_OS_IPHONE|os\(iOS\)|Platform\.isIOS|UIApplication|\bUIDevice\b",
    "android": r"import android\.|androidx\.|com\.android|Platform\.isAndroid|AndroidManifest|\bandroid:name\b|Build\.VERSION",
    "any-os check": r"sys\.platform|platform\.system\(|os\.name\b|process\.platform|runtime\.GOOS|cfg\(target_os|std::env::consts::OS|#if\s+os\(|#ifdef\s+_|RuntimeInformation\.IsOSPlatform|defaultTargetPlatform|os\.uname\(",
}
RX = {k: re.compile(v) for k, v in RULES.items()}
TEXT_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".kts", ".swift", ".m", ".mm", ".h", ".c", ".cc",
            ".cpp", ".hpp", ".cs", ".go", ".rs", ".dart", ".sh", ".gradle", ".xml", ".plist", ".toml", ".yaml", ".yml", ".json", ".rb"}
MAX_HITS = 400


def scan_code(ws: Path) -> dict:
    hits, counts, files = [], {k: 0 for k in RULES}, {k: set() for k in RULES}
    for p in tech.walk_files(ws, 400_000):
        rel = str(p.relative_to(ws))
        if p.suffix.lower() not in TEXT_EXT or rel.endswith(("package-lock.json", "yarn.lock")):
            continue
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        if p.name == "AndroidManifest.xml":
            counts["android"] += 1; files["android"].add(rel); hits.append({"cat": "android", "file": rel, "line": 1, "text": "AndroidManifest.xml"})
        for i, line in enumerate(lines, 1):
            if len(line) > 400:
                continue
            for cat, rx in RX.items():
                if rx.search(line):
                    counts[cat] += 1
                    files[cat].add(rel)
                    if len(hits) < MAX_HITS:
                        hits.append({"cat": cat, "file": rel, "line": i, "text": line.strip()[:160]})
    return {"counts": counts, "files": {k: len(v) for k, v in files.items()}, "hits": hits, "truncated": sum(counts.values()) > len(hits)}


# ── databases & services the code actually uses (incl. ones bundled with the language, e.g. sqlite3) ──
SERVICES = [
    ("SQLite", "embedded file database, bundled with the runtime", r"\bimport sqlite3\b|\bfrom sqlite3\b|better-sqlite3|\bsqlite3\.|sqflite|SQLiteDatabase|libsqlite3|\bsqlx?::sqlite|gorm\.io/driver/sqlite"),
    ("MongoDB", "needs a MongoDB server", r"\bpymongo\b|\bmotor\b|\bmongoose\b|from ['\"]mongodb['\"]|mongodb://|mongo-driver"),
    ("PostgreSQL", "needs a PostgreSQL server", r"\bpsycopg2?\b|\basyncpg\b|postgres(ql)?://|from ['\"]pg['\"]|\bpgx\b"),
    ("MySQL", "needs a MySQL server", r"\bpymysql\b|mysql\.connector|\bmysqlclient\b|mysql://|from ['\"]mysql2?['\"]"),
    ("Redis", "needs a Redis server", r"\bimport redis\b|from redis\b|\bioredis\b|redis://"),
    ("Ollama", "needs the Ollama app/server", r"\bimport ollama\b|from ollama\b|localhost:11434|ollama/"),
    ("PyTorch", "large ML runtime (CPU/GPU builds differ per OS)", r"\bimport torch\b|from torch\b"),
    ("ONNX Runtime", "native ML runtime (per-OS binaries)", r"\bonnxruntime\b"),
    ("OpenCV", "native image library", r"\bimport cv2\b|from cv2\b"),
]
SRX = [(n, d, re.compile(x)) for n, d, x in SERVICES]


def services(ws: Path) -> list[dict]:
    found: dict[str, dict] = {}
    for p in tech.walk_files(ws, 400_000):
        if p.suffix.lower() not in {".py", ".js", ".mjs", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".dart", ".swift", ".cs"}:
            continue
        try:
            txt = p.read_text(errors="ignore")
        except OSError:
            continue
        for n, d, rx in SRX:
            if rx.search(txt):
                f = found.setdefault(n, {"name": n, "note": d, "files": []})
                f["files"].append(str(p.relative_to(ws)))
    return [{**v, "count": len(v["files"]), "files": v["files"][:8]} for v in found.values()]


def changes(ws: Path, sha: str, prev: str | None) -> dict:
    import subprocess
    git = lambda *a: subprocess.run(["git", "-C", str(ws), *a], capture_output=True, text=True, timeout=20).stdout  # noqa: E731
    head = git("log", "-1", "--format=%H%x1f%an%x1f%aI%x1f%B", sha).split("\x1f")
    version = ""
    try:
        if (ws / "pyproject.toml").exists():
            m = re.search(r'^version\s*=\s*"([^"]+)"', git("show", f"{sha}:pyproject.toml"), re.M)
            version = m.group(1) if m else ""
        elif (ws / "package.json").exists():
            version = json.loads(git("show", f"{sha}:package.json") or "{}").get("version", "")
    except Exception:  # noqa: BLE001
        pass
    rng = f"{prev}..{sha}" if prev else f"{sha}~1..{sha}"
    commits = [dict(zip(("sha", "author", "date", "subject"), ln.split("\x1f"))) for ln in git("log", "--format=%h%x1f%an%x1f%aI%x1f%s", "-50", rng).splitlines() if ln]
    files = []
    for ln in git("diff", "--numstat", rng).splitlines():
        a, d, f = (ln.split("\t") + ["", "", ""])[:3]
        files.append({"file": f, "add": a, "del": d})
    return {"commit": head[0] if head else sha, "author": head[1] if len(head) > 3 else "", "date": head[2] if len(head) > 3 else "",
            "message": head[3].strip() if len(head) > 3 else "", "version": version, "previous": prev,
            "commits": commits, "files": files[:200], "file_count": len(files)}


def analyze(ws: Path, tool: str | None) -> dict:
    deps = dependencies(ws, tool)
    return {"services": services(ws), "tool": tool, "dependencies": deps[:200], "total": len(deps), "total_size": sum(d["size"] or 0 for d in deps),
            "os_specific": [d["name"] for d in deps if d["platforms"]][:50], "code": scan_code(ws)}
