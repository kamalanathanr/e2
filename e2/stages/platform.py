"""Stage 3 - Target Platform: produce an artifact per selected target (desktop and mobile).

Each target ends up in runs/<n>/artifacts/<target>/. Targets this machine cannot build
(e.g. a Windows-only toolchain on a Mac) are reported as 'unsupported' instead of failing silently."""
import shutil
import zipfile
from pathlib import Path

from . import build, tech

TARGETS = ["mac", "windows", "linux", "ios", "android"]
DESKTOP = {"mac", "windows", "linux"}


class Unsupported(Exception):
    pass


async def run(ctx, key: str) -> dict:
    ws: Path = ctx.workspace
    want = [t for t in TARGETS if ctx.config["platform"].get(t)]
    if not want:
        raise ValueError("No target platform selected")
    tool = ctx.run["stages"]["build"].get("detail", {}).get("tool") or tech.pick(ws, "auto")
    art: Path = ctx.artifacts
    if art.exists():
        shutil.rmtree(art)  # a resumed run rebuilds its artifacts from scratch
    results, made = {}, 0
    for t in want:
        out = art / t
        out.mkdir(parents=True, exist_ok=True)
        ctx.log(key, f"── {t} ──")
        try:
            fn = PACKAGERS.get(tool, _portable)
            if tool == "python" and ctx.config["platform"].get("installer") and t in ("mac", "windows"):
                fn = _python_app
            files = await fn(ctx, key, ws, t, out)
            if not files:
                raise RuntimeError("nothing was produced")
            results[t] = {"status": "built", "artifacts": [f.name for f in files]}
            made += len(files)
            for f in files:
                ctx.log(key, f"  → {t}/{f.name}")
        except Unsupported as e:
            results[t] = {"status": "unsupported", "note": str(e)}
            ctx.log(key, f"  unsupported: {e}")
            shutil.rmtree(out, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            results[t] = {"status": "failed", "note": str(e)[:300]}
            ctx.log(key, f"  FAILED: {e}")
            shutil.rmtree(out, ignore_errors=True)
    if made:
        docs(ctx, key, ws, art)
    line = " · ".join(f"{t} {'✓' if r['status'] == 'built' else '✕' if r['status'] == 'failed' else '–'}" for t, r in results.items())
    failed = [t for t, r in results.items() if r["status"] == "failed"]
    if failed:
        raise RuntimeError(f"Failed for {', '.join(failed)} ({line})")
    if not made:
        raise RuntimeError(f"No selected target can be built on this machine ({line})")
    return {"summary": line, "detail": {"targets": results, "host": f"{tech.HOST}/{tech.ARCH}"}}


def repo_file(ws: Path, rel: str, what: str) -> Path | None:
    """A file from the checked-out source, by its path relative to the repository root."""
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        raise RuntimeError(f"{what}: no file name set")
    p = (ws / rel).resolve()
    if not p.is_relative_to(ws.resolve()):
        raise RuntimeError(f"{what}: '{rel}' is outside the repository")
    return p if p.is_file() else None


def docs(ctx, key, ws: Path, art: Path) -> None:
    """Release documents taken from the checked-out source (each one can be switched off in the stage options)."""
    cfg = ctx.config["platform"]
    if cfg.get("readme", True):
        name = cfg.get("readme_path") or "README.md"
        src = repo_file(ws, name, "README")
        if not src:
            raise RuntimeError(f"README: '{name}' not found in the repository root. Add it, change the file name, or untick Include README")
        shutil.copy2(src, art / src.name)
        ctx.log(key, f"  → {src.name} (from the source)")
    if cfg.get("icon"):
        from .pyapp import icon_source
        shutil.copy2(icon_source(ws, cfg), art / "icon.png")
        ctx.log(key, "  → icon.png (1024 × 1024, from the source)")
    if cfg.get("release_notes", True):
        name = cfg.get("release_notes_path") or "RELEASE_NOTES.md"
        src = repo_file(ws, name, "Release notes")
        if src:
            shutil.copy2(src, art / "RELEASE_NOTES.md")
            ctx.log(key, f"  → RELEASE_NOTES.md (from the source: {name})")
            return
        ctx.log(key, f"  '{name}' not in the repository; generating release notes from the commits")
        from .. import store
        from .deps import changes
        commit = ctx.run["stages"]["source"].get("detail", {}).get("commit")
        lines = [f"# {(store.project(ctx.pid) or {}).get('name') or ctx.pid} v{ctx.run['version']}", "",
                 f"Built by E2 run #{ctx.run['number']} on {store.now()[:10]} for: "
                 + ", ".join(t for t in TARGETS if cfg.get(t)), ""]
        if commit and (ws / ".git").exists():
            prev = next((x["stages"]["source"].get("detail", {}).get("commit") for x in store.runs(ctx.pid, limit=200)
                         if x["number"] < ctx.run["number"] and x["status"] == "success"
                         and x["stages"]["source"].get("detail", {}).get("commit") not in (None, commit)), None)
            c = changes(ws, commit, prev)
            lines += [f"Source commit: `{c['commit'][:10]}` by {c['author']} ({c['date'][:10]})", "", "## What changed", ""]
            lines += [f"- {x['subject']} (`{x['sha']}`)" for x in c["commits"]] or ["- (no new commits)"]
            if c["message"]:
                lines += ["", "## Details", "", c["message"]]
            if c["files"]:
                lines += ["", f"## Files changed ({c['file_count']})", ""] + [f"- {f['file']} (+{f['add']} / -{f['del']})" for f in c["files"][:100]]
        (art / "RELEASE_NOTES.md").write_text("\n".join(lines) + "\n")
        ctx.log(key, "  → RELEASE_NOTES.md (generated from the commits since the last successful build)")


def _name(ctx) -> str:
    return f"{ctx.pid}-{ctx.run['version']}"


def _zip(src: Path, dst: Path, arcroot: str | None = None) -> Path:
    """Zip a folder (or single file). Uses ditto on macOS so .app bundles keep symlinks and permissions."""
    if tech.has("ditto") and src.is_dir() and src.suffix == ".app":
        import subprocess
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(src), str(dst)], check=True)
        return dst
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        if src.is_file():
            z.write(src, src.name)
        else:
            for p in sorted(src.rglob("*")):
                z.write(p, Path(arcroot or src.name) / p.relative_to(src))
    return dst


def _ipa(app: Path, dst: Path) -> Path:
    """An .ipa is a zip with Payload/<App>.app inside."""
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(app.rglob("*")):
            z.write(p, Path("Payload") / app.name / p.relative_to(app))
    return dst


async def _portable(ctx, key, ws, t, out) -> list[Path]:
    """JVM jars, Python wheels, web builds: one portable artifact that runs on every desktop OS."""
    if t not in DESKTOP:
        if ctx.run["stages"]["build"].get("detail", {}).get("tool") in ("gradle",) and tech.is_android(ws) and t == "android":
            return await _android_gradle(ctx, key, ws, t, out)
        raise Unsupported(f"{t} needs a mobile toolchain (Flutter, Xcode or Android Gradle); this project is not one")
    outputs = build.collect(ws, ctx.run["stages"]["build"].get("detail", {}).get("tool"))
    if not outputs:
        raise RuntimeError("the Build stage produced no outputs to package")
    files = []
    for o in outputs:
        if o.is_file():
            dst = out / o.name
            shutil.copy2(o, dst)
        else:
            dst = _zip(o, out / f"{_name(ctx)}-{t}-{o.name}.zip", arcroot=_name(ctx))
        files.append(dst)
    ctx.log(key, "  portable artifact (same content for every desktop OS)")
    return files


async def _go(ctx, key, ws, t, out):
    goos = {"mac": "darwin", "windows": "windows", "linux": "linux"}.get(t)
    if not goos:
        raise Unsupported("Go mobile builds need gomobile; not set up")
    arch = tech.ARCH if t == tech.HOST else "amd64"
    tmp = out / "_bin"
    tmp.mkdir(exist_ok=True)
    await ctx.sh(key, "go", "build", "-trimpath", "-ldflags", f"-X main.version={ctx.run['version']}", "-o", f"{tmp}/", "./...",
                 cwd=ws, env={"GOOS": goos, "GOARCH": arch, "CGO_ENABLED": "0"})
    bins = [p for p in tmp.iterdir() if p.is_file()]
    if not bins:
        shutil.rmtree(tmp)
        raise RuntimeError("no main package found (nothing to run)")
    files = []
    for b in bins:
        dst = out / f"{b.stem}-{ctx.run['version']}-{goos}-{arch}{b.suffix}"
        b.rename(dst)
        files.append(dst)
    shutil.rmtree(tmp)
    return files


async def _rust(ctx, key, ws, t, out):
    if t == tech.HOST:
        bins = build.collect(ws, "rust")
    else:
        triple = {"windows": "x86_64-pc-windows-gnu", "linux": "x86_64-unknown-linux-gnu",
                  "ios": "aarch64-apple-ios", "android": "aarch64-linux-android"}[t]
        installed = await ctx.sh(key, "rustup", "target", "list", "--installed", quiet=True, check=False) if tech.has("rustup") else ""
        if triple not in installed:
            raise Unsupported(f"Rust target {triple} not installed (rustup target add {triple}, plus a linker)")
        await ctx.sh(key, "cargo", "build", "--release", "--target", triple, cwd=ws)
        rel = ws / "target" / triple / "release"
        bins = [p for p in rel.glob("*") if p.is_file() and (p.suffix == ".exe" or (p.stat().st_mode & 0o111 and not p.suffix))]
    files = []
    for b in bins:
        dst = out / f"{b.stem}-{ctx.run['version']}-{t}{b.suffix}"
        shutil.copy2(b, dst)
        files.append(dst)
    return files


async def _dotnet(ctx, key, ws, t, out):
    rid = {"mac": f"osx-{'arm64' if tech.ARCH == 'arm64' else 'x64'}", "windows": "win-x64", "linux": "linux-x64"}.get(t)
    if not rid:
        raise Unsupported(".NET mobile (MAUI) builds are not set up")
    pub = out / "_pub"
    await ctx.sh(key, "dotnet", "publish", "-c", "Release", "-r", rid, "--self-contained", "false",
                 f"-p:Version={ctx.run['version']}", "-o", pub, cwd=ws)
    z = _zip(pub, out / f"{_name(ctx)}-{rid}.zip", arcroot=_name(ctx))
    shutil.rmtree(pub)
    return [z]


async def _flutter(ctx, key, ws, t, out):
    v = ctx.run["version"]
    ver = ["--build-name", v, "--build-number", str(ctx.run["number"])]
    if t in ("windows", "linux") and t != tech.HOST:
        raise Unsupported(f"Flutter builds {t} apps only on a {t} machine")
    if t == "ios" and tech.HOST != "mac":
        raise Unsupported("iOS builds need a Mac with Xcode")
    if t == "mac":
        await ctx.sh(key, "flutter", "build", "macos", "--release", *ver, cwd=ws)
        apps = list((ws / "build/macos/Build/Products/Release").glob("*.app"))
        return [_zip(a, out / f"{_name(ctx)}-macos.zip") for a in apps]
    if t == "ios":
        await ctx.sh(key, "flutter", "build", "ios", "--release", "--no-codesign", *ver, cwd=ws)
        apps = list((ws / "build/ios/iphoneos").glob("*.app"))
        ctx.log(key, "  note: unsigned .ipa. App Store upload needs signing (set a team in Xcode)")
        return [_ipa(a, out / f"{_name(ctx)}-ios.ipa") for a in apps]
    if t == "android":
        await ctx.sh(key, "flutter", "build", "apk", "--release", *ver, cwd=ws)
        await ctx.sh(key, "flutter", "build", "appbundle", "--release", *ver, cwd=ws, check=False)
        files = []
        for src, ext in ((ws / "build/app/outputs/flutter-apk/app-release.apk", "apk"),
                         (ws / "build/app/outputs/bundle/release/app-release.aab", "aab")):
            if src.exists():
                files.append(Path(shutil.copy2(src, out / f"{_name(ctx)}-android.{ext}")))
        return files
    await ctx.sh(key, "flutter", "build", t, "--release", *ver, cwd=ws)
    return [_zip(ws / "build" / t, out / f"{_name(ctx)}-{t}.zip")]


async def _xcode(ctx, key, ws, t, out):
    if t not in ("mac", "ios"):
        raise Unsupported("Xcode builds only macOS and iOS apps")
    proj = next(iter(ws.glob("*.xcworkspace")), None) or next(iter(ws.glob("*.xcodeproj")), None)
    flag = "-workspace" if proj.suffix == ".xcworkspace" else "-project"
    import json
    info = json.loads(await ctx.sh(key, "xcodebuild", "-list", "-json", flag, proj.name, cwd=ws, quiet=True))
    schemes = (info.get("workspace") or info.get("project") or {}).get("schemes") or []
    if not schemes:
        raise RuntimeError("no Xcode scheme found")
    sdk = ["-sdk", "iphoneos"] if t == "ios" else []
    await ctx.sh(key, "xcodebuild", flag, proj.name, "-scheme", schemes[0], "-configuration", "Release", *sdk,
                 "-derivedDataPath", ".e2build", f"MARKETING_VERSION={ctx.run['version']}",
                 f"CURRENT_PROJECT_VERSION={ctx.run['number']}", "CODE_SIGNING_ALLOWED=NO", "build", cwd=ws)
    prod = ws / ".e2build/Build/Products" / ("Release-iphoneos" if t == "ios" else "Release")
    apps = list(prod.glob("*.app"))
    if t == "ios":
        return [_ipa(a, out / f"{_name(ctx)}-ios.ipa") for a in apps]
    return [_zip(a, out / f"{_name(ctx)}-macos.zip") for a in apps]


async def _android_gradle(ctx, key, ws, t, out):
    await ctx.sh(key, tech.gradle(ws), "--no-daemon", "assembleRelease", cwd=ws)
    files = []
    for apk in ws.glob("**/build/outputs/apk/release/*.apk"):
        files.append(Path(shutil.copy2(apk, out / f"{_name(ctx)}-{apk.name}")))
    return files


async def _gradle(ctx, key, ws, t, out):
    if tech.is_android(ws):
        if t != "android":
            raise Unsupported("this is an Android project")
        return await _android_gradle(ctx, key, ws, t, out)
    return await _portable(ctx, key, ws, t, out)


async def _python_app(ctx, key, ws, t, out):
    from .. import store
    from . import pyapp
    app = (ctx.config["platform"].get("app_name") or "").strip() or (store.project(ctx.pid) or {}).get("name") or ctx.pid.title()
    return await (pyapp.build_mac if t == "mac" else pyapp.build_windows)(ctx, key, ws, out, app)


PACKAGERS = {"go": _go, "rust": _rust, "dotnet": _dotnet, "flutter": _flutter, "xcode": _xcode, "gradle": _gradle}
