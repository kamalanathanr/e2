"""Stage 5 - Verify Artifact: integrity (SHA-256), signatures, and a smoke test. Writes manifest.json."""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .. import store
from . import tech

META = {"SHA256SUMS", "manifest.json"}


def artifacts(art: Path) -> list[Path]:
    return sorted(p for p in art.rglob("*") if p.is_file() and p.name not in META) if art.exists() else []


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def run(ctx, key: str) -> dict:
    cfg, art = ctx.config["verify"], ctx.artifacts
    files = artifacts(art)
    if not files:
        raise RuntimeError("No artifacts to verify. The Target Platform stage must produce at least one")
    empty = [f.name for f in files if f.stat().st_size == 0]
    if empty:
        raise RuntimeError(f"Empty artifact(s): {', '.join(empty)}")
    ctx.log(key, f"{len(files)} artifact(s)")
    sums = {str(f.relative_to(art)): await asyncio.to_thread(sha256, f) for f in files}
    checks, problems = {}, []

    if cfg.get("checksum", True):
        (art / "SHA256SUMS").write_text("".join(f"{h}  {n}\n" for n, h in sums.items()))
        for line in (art / "SHA256SUMS").read_text().splitlines():  # read back and re-hash: proves the files on disk match
            h, n = line.split("  ", 1)
            if await asyncio.to_thread(sha256, art / n) != h:
                problems.append(f"checksum mismatch: {n}")
        for n, h in sums.items():
            ctx.log(key, f"  sha256 {h}  {n}")
        checks["checksum"] = "failed" if problems else "passed"

    if cfg.get("signature"):
        sig = {}
        for f in files:
            st, note = await asyncio.to_thread(_signature, f)
            sig[f.name] = f"{st}: {note}" if note else st
            ctx.log(key, f"  signature {f.name}: {st}{' · ' + note if note else ''}")
            if st in ("unsigned", "invalid", "unverifiable"):
                problems.append(f"{f.name} {st}")
        checks["signature"] = sig

    if cfg.get("smoke"):
        smoke = {}
        for f in files:
            st, note = await asyncio.to_thread(_smoke, f)
            smoke[f.name] = f"{st}: {note}" if note else st
            ctx.log(key, f"  smoke {f.name}: {st}{' · ' + note if note else ''}")
            if st == "failed":
                problems.append(f"{f.name} smoke test failed")
        checks["smoke"] = smoke

    src = ctx.run["stages"]["source"].get("detail", {})
    manifest = {"project": ctx.pid, "version": ctx.run["version"], "run": ctx.run["number"],
                "commit": src.get("commit"), "branch": src.get("branch"), "created": store.now(),
                "artifacts": [{"path": n, "sha256": h, "size": (art / n).stat().st_size} for n, h in sums.items()]}
    (art / "manifest.json").write_text(json.dumps(manifest, indent=2))
    ctx.log(key, "Wrote manifest.json")
    if problems:
        ctx.run["stages"][key]["detail"] = {"checks": checks}
        raise RuntimeError("; ".join(problems))
    return {"summary": f"{len(files)} artifact(s) verified", "detail": {"artifacts": list(sums), "checks": checks}}


def _out(args, timeout=60) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{args[0]} not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def _signature(f: Path) -> tuple[str, str]:
    ext = f.suffix.lower()
    if ext in (".dmg", ".app"):
        rc, out = _out(["codesign", "-v", "--deep", "--strict", str(f)])
        return ("signed", "") if rc == 0 else ("unsigned" if "not signed" in out else "invalid", out[-200:])
    if ext == ".pkg":
        rc, out = _out(["pkgutil", "--check-signature", str(f)])
        return ("signed", "") if rc == 0 else ("unsigned", "")
    if ext in (".zip", ".ipa"):
        with tempfile.TemporaryDirectory() as d:
            with zipfile.ZipFile(f) as z:
                apps = sorted({n.split(".app/")[0] + ".app" for n in z.namelist() if ".app/" in n}, key=len)
                if not apps:
                    return "not-applicable", "archive has no app bundle"
                if not tech.has("ditto"):
                    return "unverifiable", "ditto not available"
            subprocess.run(["ditto", "-x", "-k", str(f), d], check=True)
            rc, out = _out(["codesign", "-v", "--deep", "--strict", str(Path(d) / apps[0])])
            return ("signed", apps[0]) if rc == 0 else ("unsigned" if "not signed" in out else "invalid", out[-200:])
    if ext in (".jar", ".war"):
        rc, out = _out(["jarsigner", "-verify", str(f)])
        if rc == 127:
            return "unverifiable", out
        return ("signed", "") if "jar verified" in out and "unsigned" not in out else ("unsigned", "")
    if ext in (".apk", ".aab"):
        rc, out = _out(["apksigner", "verify", str(f)] if ext == ".apk" else ["jarsigner", "-verify", str(f)])
        return ("unverifiable", out) if rc == 127 else ("signed", "") if rc == 0 else ("unsigned", out[-200:])
    if ext in (".exe", ".msi", ".dll"):
        rc, out = _out(["osslsigncode", "verify", str(f)])
        return ("unverifiable", "install osslsigncode to verify Windows signatures") if rc == 127 else ("signed", "") if rc == 0 else ("unsigned", "")
    if os.access(f, os.X_OK) and tech.HOST == "mac":
        rc, out = _out(["codesign", "-v", str(f)])
        return ("signed", "") if rc == 0 else ("unsigned", "")
    return "not-applicable", "file type is not signed"


def _smoke(f: Path) -> tuple[str, str]:
    """Start the artifact if this machine can, and check it doesn't crash immediately."""
    ext = f.suffix.lower()
    if ext in (".jar",):
        with zipfile.ZipFile(f) as z:
            mf = z.read("META-INF/MANIFEST.MF").decode(errors="ignore") if "META-INF/MANIFEST.MF" in z.namelist() else ""
        if "Main-Class" not in mf:
            return "skipped", "library jar (no Main-Class)"
        return _launch(["java", "-jar", str(f), "--version"])
    if ext == ".whl":
        with tempfile.TemporaryDirectory() as d:
            rc, out = _out([shutil.which(tech.PY) or tech.PY, "-m", "venv", d])
            rc, out = _out([str(Path(d) / tech.VENV_BIN / "python"), "-m", "pip", "install", "-q", str(f)], timeout=300)
            return ("passed", "wheel installs cleanly") if rc == 0 else ("failed", out[-200:])
    if os.access(f, os.X_OK) and ext not in (".zip", ".ipa", ".apk", ".aab", ".dmg", ".pkg", ".tar", ".gz", ".msi"):
        if tech.WIN:
            return "skipped", "no automatic smoke test for this type"
        rc, out = _out(["file", "-b", str(f)])
        native = {"mac": "Mach-O", "linux": "ELF"}.get(tech.HOST, "")
        if native not in out:
            return "skipped", f"not runnable here ({out[:40]})"
        return _launch([str(f), "--version"])
    return "skipped", "no automatic smoke test for this type"


def _launch(args) -> tuple[str, str]:
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        return "skipped", f"{args[0]} not installed"
    try:
        out, _ = p.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        p.kill()
        return "passed", "still running after 8s (started fine)"
    return ("passed", out.strip()[:80]) if p.returncode in (0, 1, 2) and "Exception" not in out and "panic" not in out else ("failed", out[-200:])
