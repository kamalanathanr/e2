"""Stage 4 - Test: each ticked check runs on its own and reports passed / failed / skipped / warning.

A check is 'skipped' when the project has nothing for it (e.g. no integration suite); only
'failed' fails the stage. Reports for security and license review are saved in the run folder."""
import json
import re
from pathlib import Path

from .. import store
from . import tech

ORDER = ["unit", "integration", "regression", "docker", "security", "license"]


async def run(ctx, key: str) -> dict:
    ws: Path = ctx.workspace
    cfg = ctx.config["test"]
    checks = [c for c in ORDER if cfg.get(c)]
    if not checks:
        ctx.log(key, "No test checks selected")
        return {"status": "skipped", "summary": "No checks selected"}
    tool = ctx.run["stages"]["build"].get("detail", {}).get("tool") or tech.pick(ws, "auto")
    results = {}
    for c in checks:
        ctx.log(key, f"══ {c} ══")
        try:
            status, note = await CHECKS[c](ctx, key, ws, tool)
        except Exception as e:  # noqa: BLE001
            status, note = "failed", str(e)[:300]
        results[c] = {"status": status, "note": note}
        ctx.log(key, f"→ {c}: {status.upper()}{' · ' + note if note else ''}")
    failed = [c for c, r in results.items() if r["status"] == "failed"]
    summary = " · ".join(f"{c} {_mark(r['status'])}" for c, r in results.items())
    if failed:
        ctx.run["stages"][key]["detail"] = {"checks": results}
        raise RuntimeError(f"Failed: {', '.join(failed)} ({summary})")
    return {"summary": summary, "detail": {"checks": results}}


def _mark(s): return {"passed": "✓", "failed": "✕", "skipped": "–", "warning": "⚠"}[s]


async def _rc(ctx, key, *args, cwd, env=None) -> int:
    """Run and return the exit code (does not raise)."""
    try:
        await ctx.sh(key, *args, cwd=cwd, env=env, check=False)
        return ctx.last_rc
    except FileNotFoundError:
        raise RuntimeError(f"`{args[0]}` is not installed")


def _has_tests_py(ws):
    return (ws / "tests").is_dir() or (ws / "test").is_dir() or any(ws.glob("test_*.py")) or any(ws.glob("**/test_*.py"))


async def _pytest(ctx, key, ws, *paths):
    if tech.py_mode(ws) == "uv":
        rc = await _rc(ctx, key, "uv", "run", "--with", "pytest", "pytest", "-q", *paths, cwd=ws)
    else:
        env = tech.py_env(ws)
        if not (ws / ".e2venv").exists():
            await ctx.sh(key, tech.PY, "-m", "venv", ".e2venv", cwd=ws)
        await ctx.sh(key, "python", "-m", "pip", "install", "-q", "pytest", cwd=ws, env=env)
        rc = await _rc(ctx, key, "python", "-m", "pytest", "-q", *paths, cwd=ws, env=env)
    return ("passed", "") if rc == 0 else ("skipped", "no tests collected") if rc == 5 else ("failed", f"pytest exit {rc}")


async def unit(ctx, key, ws, tool):
    s = tech.scripts(ws)
    if tool == "maven":
        rc = await _rc(ctx, key, tech.mvn(ws), "-B", "-ntp", "test", cwd=ws)
    elif tool == "gradle":
        rc = await _rc(ctx, key, tech.gradle(ws), "--no-daemon", "test", cwd=ws)
    elif tool == "npm":
        if "test" not in s or "no test specified" in s["test"]:
            return "skipped", "no test script in package.json"
        rc = await _rc(ctx, key, tech.node_pm(ws), "test", cwd=ws, env={"CI": "true"})
    elif tool == "python":
        if not _has_tests_py(ws):
            return "skipped", "no tests found"
        return await _pytest(ctx, key, ws)
    elif tool == "go":
        rc = await _rc(ctx, key, "go", "test", "./...", cwd=ws)
    elif tool == "rust":
        rc = await _rc(ctx, key, "cargo", "test", cwd=ws)
    elif tool == "dotnet":
        rc = await _rc(ctx, key, "dotnet", "test", "-c", "Release", cwd=ws)
    elif tool == "flutter":
        if not (ws / "test").is_dir():
            return "skipped", "no test/ folder"
        rc = await _rc(ctx, key, "flutter", "test", cwd=ws)
    elif tool == "make" and re.search(r"^test:", (ws / "Makefile").read_text(errors="ignore"), re.M):
        rc = await _rc(ctx, key, "make", "test", cwd=ws)
    else:
        return "skipped", f"no unit test runner known for {tool}"
    return ("passed", "") if rc == 0 else ("failed", f"exit code {rc}")


async def integration(ctx, key, ws, tool):
    s = tech.scripts(ws)
    if tool == "maven":
        rc = await _rc(ctx, key, tech.mvn(ws), "-B", "-ntp", "verify", cwd=ws)
    elif tool == "gradle":
        tasks = await ctx.sh(key, tech.gradle(ws), "--no-daemon", "tasks", "--all", cwd=ws, quiet=True, check=False)
        if "integrationTest" not in tasks:
            return "skipped", "no integrationTest task"
        rc = await _rc(ctx, key, tech.gradle(ws), "--no-daemon", "integrationTest", cwd=ws)
    elif tool == "npm":
        name = next((n for n in ("test:integration", "test:e2e", "integration", "e2e") if n in s), None)
        if not name:
            return "skipped", "no test:integration / test:e2e script"
        rc = await _rc(ctx, key, tech.node_pm(ws), "run", name, cwd=ws, env={"CI": "true"})
    elif tool == "python":
        d = next((p for p in ("tests/integration", "tests/it", "integration_tests") if (ws / p).is_dir()), None)
        if not d:
            return "skipped", "no tests/integration folder"
        return await _pytest(ctx, key, ws, d)
    elif tool == "go":
        if not any("//go:build integration" in p.read_text(errors="ignore") for p in ws.glob("**/*_test.go")):
            return "skipped", "no files tagged //go:build integration"
        rc = await _rc(ctx, key, "go", "test", "-tags", "integration", "./...", cwd=ws)
    elif tool == "flutter" and (ws / "integration_test").is_dir():
        return "skipped", "integration_test needs a device or emulator"
    else:
        return "skipped", f"no integration suite convention for {tool}"
    return ("passed", "") if rc == 0 else ("failed", f"exit code {rc}")


async def regression(ctx, key, ws, tool):
    """Runs a regression suite if the project has one, and always compares outputs with the last good run."""
    notes, status = [], "passed"
    s = tech.scripts(ws)
    name = next((n for n in ("test:regression", "regression") if n in s), None)
    if name:
        rc = await _rc(ctx, key, tech.node_pm(ws), "run", name, cwd=ws, env={"CI": "true"})
        status = "passed" if rc == 0 else "failed"
        notes.append(f"suite exit {rc}")
    elif (ws / "tests" / "regression").is_dir() and tool == "python":
        status, n = await _pytest(ctx, key, ws, "tests/regression")
        notes.append(n or "suite ran")
    prev = next((r for r in store.runs(ctx.pid, 50) if r["id"] != ctx.rid and r["status"] == "success"), None)
    now_out = set(ctx.run["stages"]["build"].get("detail", {}).get("outputs", []))
    if prev:
        ver = lambda n: re.sub(r"\d+\.\d+\.\d+", "X", n)  # noqa: E731 - ignore version numbers in names
        before = {ver(o) for o in prev["stages"]["build"].get("detail", {}).get("outputs", [])}
        missing = before - {ver(o) for o in now_out}
        ctx.log(key, f"Comparing build outputs with run #{prev['number']} (v{prev['version']})")
        if missing:
            ctx.log(key, "Missing compared to last good run: " + ", ".join(sorted(missing)))
            return "failed", f"{len(missing)} output(s) missing vs run #{prev['number']}"
        notes.append(f"outputs match run #{prev['number']}")
    else:
        notes.append("no earlier successful run to compare with")
    if not name and status == "passed" and not prev:
        return "skipped", "no regression suite and nothing to compare with yet"
    return status, "; ".join(notes)


DOCKER_IMAGES = {
    "maven": ("maven:3-eclipse-temurin-21", "mvn -B -ntp test"),
    "gradle": ("gradle:8-jdk21", "gradle --no-daemon test"),
    "npm": ("node:22", "npm ci --no-audit --no-fund && npm test"),
    "python": ("python:3.12", "pip install -q -r requirements.txt pytest 2>/dev/null || pip install -q . pytest; pytest -q"),
    "go": ("golang:1.23", "go test ./..."),
    "rust": ("rust:1", "cargo test"),
}


async def docker(ctx, key, ws, tool):
    tech.require("docker")
    if await _rc(ctx, key, "docker", "info", "--format", "{{.ServerVersion}}", cwd=ws) != 0:
        return "failed", "Docker daemon is not running"
    tag = f"e2-{ctx.pid}:{ctx.run['version']}"
    if (ws / "Dockerfile.test").exists():
        if await _rc(ctx, key, "docker", "build", "-f", "Dockerfile.test", "-t", tag + "-test", ".", cwd=ws) != 0:
            return "failed", "Dockerfile.test did not build"
        rc = await _rc(ctx, key, "docker", "run", "--rm", tag + "-test", cwd=ws)
        return ("passed", "Dockerfile.test") if rc == 0 else ("failed", f"tests in container exit {rc}")
    if tool in DOCKER_IMAGES:
        img, cmd = DOCKER_IMAGES[tool]
        if tool == "npm" and "test" not in tech.scripts(ws):
            return "skipped", "no test script"
        rc = await _rc(ctx, key, "docker", "run", "--rm", "-v", f"{ws}:/src", "-w", "/src", img, "sh", "-c", cmd, cwd=ws)
        if tool == "python" and rc == 5:
            return "skipped", "no tests collected"
        return ("passed", f"unit tests in {img}") if rc == 0 else ("failed", f"exit {rc} in {img}")
    if (ws / "Dockerfile").exists():
        rc = await _rc(ctx, key, "docker", "build", "-t", tag, ".", cwd=ws)
        return ("passed", "Dockerfile builds (add Dockerfile.test to run tests inside)") if rc == 0 else ("failed", "docker build failed")
    return "skipped", f"no Dockerfile and no standard image for {tool}"


SECRET_STRONG = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Stripe live key", re.compile(r"\bsk_live_[0-9a-zA-Z]{20,}")),
]
SECRET_WEAK = re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")


async def security(ctx, key, ws, tool):
    findings, fail = [], False
    for p in tech.walk_files(ws):
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(ws))
        for i, line in enumerate(text.splitlines(), 1):
            for name, rx in SECRET_STRONG:
                if rx.search(line):
                    findings.append({"type": "secret", "severity": "high", "what": name, "where": f"{rel}:{i}"})
                    fail = True
            if SECRET_WEAK.search(line) and not re.search(r"(?i)example|dummy|changeme|placeholder|\$\{", line):
                findings.append({"type": "secret", "severity": "medium", "what": "hard-coded credential?", "where": f"{rel}:{i}"})
    ctx.log(key, f"Secret scan: {len(findings)} finding(s)")
    for f in findings[:50]:
        ctx.log(key, f"  [{f['severity']}] {f['what']} at {f['where']}")

    audits = []
    if tool == "npm" and (ws / "package-lock.json").exists():
        rc = await _rc(ctx, key, "npm", "audit", "--audit-level=high", "--omit=dev", cwd=ws)
        audits.append(("npm audit", rc))
    if tool == "python" and tech.has("uv"):
        req = ["-r", "requirements.txt"] if (ws / "requirements.txt").exists() else []
        rc = await _rc(ctx, key, "uvx", "pip-audit", *req, cwd=ws, env=tech.py_env(ws))
        audits.append(("pip-audit", rc))
    for name, args in (("govulncheck", ["govulncheck", "./..."]), ("cargo-audit", ["cargo", "audit"]),
                       ("osv-scanner", ["osv-scanner", "-r", "."]), ("gitleaks", ["gitleaks", "detect", "--no-git", "-s", "."]),
                       ("semgrep", ["semgrep", "--config", "auto", "--error", "-q"])):
        if tech.has(args[0]) and (name != "govulncheck" or tool == "go") and (name != "cargo-audit" or tool == "rust"):
            audits.append((name, await _rc(ctx, key, *args, cwd=ws)))
    for name, rc in audits:
        findings.append({"type": "audit", "severity": "high" if rc else "info", "what": f"{name} {'reported issues' if rc else 'clean'}", "where": ""})
        fail = fail or rc != 0
    _report(ctx, "security", findings)
    tools = ", ".join(a for a, _ in audits) or "no dependency auditor available for this stack"
    high = sum(f["severity"] == "high" for f in findings)
    med = sum(f["severity"] == "medium" for f in findings)
    if fail:
        return "failed", f"{high} high-severity finding(s) · {tools}"
    return ("warning" if med else "passed"), f"{med} to review · {tools}"


LICENSE_RX = [
    ("GPL", re.compile(r"GNU GENERAL PUBLIC LICENSE|SPDX-License-Identifier:\s*GPL", re.I)),
    ("AGPL", re.compile(r"GNU AFFERO|SPDX-License-Identifier:\s*AGPL", re.I)),
    ("LGPL", re.compile(r"GNU LESSER GENERAL|SPDX-License-Identifier:\s*LGPL", re.I)),
    ("SSPL", re.compile(r"Server Side Public License", re.I)),
    ("CC-BY-SA", re.compile(r"Creative Commons Attribution-ShareAlike|CC BY-SA", re.I)),
]
COPIED_RX = re.compile(r"(?i)(stackoverflow\.com/|stackexchange\.com/|copied from|taken from|borrowed from|adapted from|ported from)")
PATENT_RX = re.compile(r"(?i)\b(patent(ed)?|patent pending|U\.S\. Pat)\b")
COPYRIGHT_RX = re.compile(r"(?i)copyright\s*(?:\(c\)|©)?\s*(?:\d{4}(?:\s*[-,]\s*\d{4})*)?\s*(.{3,60})")
COPYLEFT = ("GPL", "AGPL", "SSPL", "CC-BY-SA")


async def license(ctx, key, ws, tool):  # noqa: A001 - stage option name
    f = []
    own = next((p for p in ws.iterdir() if p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING"))), None)
    own_text = own.read_text(errors="ignore") if own and own.is_file() else ""
    own_lic = next((n for n, rx in LICENSE_RX if rx.search(own_text)), "permissive/other" if own_text else None)
    if not own:
        f.append({"type": "project", "severity": "medium", "what": "No LICENSE file in the project", "where": ""})
    holders = {}
    for p in tech.walk_files(ws, 300_000):
        if p == own or p.suffix.lower() in (".lock", ".png", ".jpg", ".svg", ".ico", ".pdf"):
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(ws))
        for n, rx in LICENSE_RX:
            if rx.search(text) and n != own_lic:
                f.append({"type": "source-license", "severity": "high" if n in COPYLEFT else "medium", "what": f"{n} text in source", "where": rel})
        for i, line in enumerate(text.splitlines(), 1):
            if COPIED_RX.search(line):
                f.append({"type": "copied-code", "severity": "medium", "what": line.strip()[:120], "where": f"{rel}:{i}"})
            if PATENT_RX.search(line):
                f.append({"type": "patent", "severity": "medium", "what": line.strip()[:120], "where": f"{rel}:{i}"})
            if i <= 30 and (m := COPYRIGHT_RX.search(line)):
                holders.setdefault(m.group(1).strip(" .*/#-"), []).append(rel)
    deps = _dep_licenses(ws, tool)
    for name, lic in deps.items():
        up = (lic or "").upper()
        if not lic or up in ("UNKNOWN", "UNLICENSED", "SEE LICENSE IN LICENSE"):
            f.append({"type": "dependency", "severity": "low", "what": f"{name}: license unknown", "where": ""})
        elif any(c in up for c in ("AGPL", "SSPL")) or (re.search(r"\bGPL", up) and "LGPL" not in up):
            f.append({"type": "dependency", "severity": "high", "what": f"{name}: {lic} (copyleft)", "where": ""})
    if len(holders) > 1:
        f.append({"type": "copyright", "severity": "low", "what": f"{len(holders)} different copyright holders: " + ", ".join(list(holders)[:8]), "where": ""})
    _report(ctx, "license", f + [{"type": "dependencies", "severity": "info", "what": json.dumps(deps)[:20000], "where": ""}])
    for x in f[:60]:
        ctx.log(key, f"  [{x['severity']}] {x['type']}: {x['what']} {x['where']}")
    ctx.log(key, f"Project license: {own_lic or 'none'} · {len(deps)} dependencies checked")
    high = sum(x["severity"] == "high" for x in f)
    review = sum(x["severity"] == "medium" for x in f)
    note = f"{high} copyleft/high · {review} to review · {len(deps)} deps"
    return ("warning" if high or review else "passed"), note  # a review flags; a human decides


def _dep_licenses(ws: Path, tool) -> dict:
    out = {}
    if (ws / "node_modules").is_dir():
        for pj in list((ws / "node_modules").glob("*/package.json")) + list((ws / "node_modules").glob("@*/*/package.json")):
            try:
                j = json.loads(pj.read_text())
                lic = j.get("license")
                out[j.get("name", pj.parent.name)] = lic.get("type") if isinstance(lic, dict) else lic
            except Exception:  # noqa: BLE001
                pass
    for venv in (ws / ".e2venv", ws / ".venv"):
        for meta in (m for sp in tech.site_packages(venv) for m in sp.glob("*.dist-info/METADATA")):
            name, lic = meta.parent.name.split("-")[0], None
            for line in meta.read_text(errors="ignore").splitlines():
                if line.startswith("License-Expression:") or (line.startswith("License:") and not lic):
                    lic = line.split(":", 1)[1].strip()[:60]
                elif line.startswith("Classifier: License ::") and not lic:
                    lic = line.rsplit("::", 1)[1].strip()
                elif not line:
                    break
            out[name] = lic
    return out


def _report(ctx, name, findings):
    p = store.rdir(ctx.pid, ctx.rid) / "reports" / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(findings, indent=2))


CHECKS = {"unit": unit, "integration": integration, "regression": regression, "docker": docker,
          "security": security, "license": license}
