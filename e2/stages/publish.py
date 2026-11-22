"""Stage 6 - Publish: ship the verified artifacts to the chosen destination and record the release.

Credentials come from environment variables (never stored in project files):
  Artifactory/Nexus  E2_ARTIFACTORY_TOKEN  or  E2_ARTIFACTORY_USER + E2_ARTIFACTORY_PASSWORD
  App Store Connect  E2_ASC_KEY_ID + E2_ASC_ISSUER_ID  (key in ~/.appstoreconnect/private_keys/)
  Google Play        E2_PLAY_JSON_KEY (service-account json path), uses fastlane
  GitHub / Docker    whatever `gh auth` / `docker login` already have
"""
import asyncio
import base64
import os
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from .. import store
from . import tech
from .verify import artifacts, sha256


async def run(ctx, key: str) -> dict:
    cfg, art = ctx.config["publish"], ctx.artifacts
    files = artifacts(art)
    if not files:
        raise RuntimeError("No artifacts to publish")
    if not (art / "manifest.json").exists():
        ctx.log(key, "warning: artifacts were not verified (no manifest.json)")
    dest, target, ver = cfg.get("destination", "local"), (cfg.get("target") or "").strip(), ctx.run["version"]
    ctx.log(key, f"Publishing v{ver} ({len(files)} file(s)) to {dest} {target}")
    where = await DESTS[dest](ctx, key, art, files, target, ver)
    rel = {"version": ver, "run": ctx.run["number"], "destination": dest, "where": where, "at": store.now()}
    p = store.project(ctx.pid)
    store.update_project(ctx.pid, releases=[rel] + [r for r in p.get("releases", []) if not (r["version"] == ver and r["destination"] == dest)][:99])
    ctx.log(key, f"Published → {where}")
    return {"summary": f"v{ver} → {dest}: {where}", "detail": {"destination": dest, "where": where, "files": [f.name for f in files]}}


async def _local(ctx, key, art, files, target, ver):
    base = Path(os.path.expanduser(target)) if target else store.DATA / "releases"
    dst = base / ctx.pid / ver
    if dst.exists():
        shutil.rmtree(dst)
    await asyncio.to_thread(shutil.copytree, art, dst)
    return str(dst)


async def _artifactory(ctx, key, art, files, target, ver):
    if not target.startswith(("http://", "https://")):
        raise ValueError("Set the destination URL, e.g. https://artifactory.example.com/artifactory/libs-release-local")
    tok, user, pw = os.environ.get("E2_ARTIFACTORY_TOKEN"), os.environ.get("E2_ARTIFACTORY_USER"), os.environ.get("E2_ARTIFACTORY_PASSWORD")
    if tok:
        auth = f"Bearer {tok}"
    elif user and pw:
        auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    else:
        raise RuntimeError("Set E2_ARTIFACTORY_TOKEN (or E2_ARTIFACTORY_USER and E2_ARTIFACTORY_PASSWORD) before starting E2")
    root = f"{target.rstrip('/')}/{ctx.pid}/{ver}"
    for f in [*files, art / "manifest.json", art / "SHA256SUMS"]:
        if not f.exists():
            continue
        url = f"{root}/{urllib.parse.quote(str(f.relative_to(art)))}"
        ctx.log(key, f"PUT {url}")
        h = await asyncio.to_thread(sha256, f)

        def put(f=f, url=url, h=h):
            req = urllib.request.Request(url, data=f.read_bytes(), method="PUT",
                                         headers={"Authorization": auth, "X-Checksum-Sha256": h})
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.status
        status = await asyncio.to_thread(put)
        ctx.log(key, f"  {status}")
    return root


async def _github(ctx, key, art, files, target, ver):
    tech.require("gh")
    if not target or "/" not in target:
        raise ValueError("Set the destination to the GitHub repository, e.g. owner/repo")
    tag = f"v{ver}"
    extra = [str(p) for p in (art / "SHA256SUMS", art / "manifest.json") if p.exists()]
    exists = await ctx.sh(key, "gh", "release", "view", tag, "--repo", target, check=False, quiet=True)
    if ctx.last_rc == 0 and exists:
        await ctx.sh(key, "gh", "release", "upload", tag, *map(str, files), *extra, "--repo", target, "--clobber")
    else:
        commit = ctx.run["stages"]["source"].get("detail", {}).get("commit")
        await ctx.sh(key, "gh", "release", "create", tag, *map(str, files), *extra, "--repo", target, "--title", tag,
                     "--notes", f"Built by E2 run #{ctx.run['number']}" + (f" from {commit[:10]}" if commit else ""),
                     *(["--target", commit] if commit else []))
    return f"https://github.com/{target}/releases/tag/{tag}"


async def _docker(ctx, key, art, files, target, ver):
    tech.require("docker")
    if not target:
        raise ValueError("Set the image name, e.g. ghcr.io/me/app or docker.io/me/app")
    if not (ctx.workspace / "Dockerfile").exists():
        raise RuntimeError("The project has no Dockerfile")
    await ctx.sh(key, "docker", "build", "-t", f"{target}:{ver}", "-t", f"{target}:latest", ".", cwd=ctx.workspace)
    await ctx.sh(key, "docker", "push", f"{target}:{ver}")
    await ctx.sh(key, "docker", "push", f"{target}:latest")
    return f"{target}:{ver}"


async def _appstore(ctx, key, art, files, target, ver):
    ipa = next((f for f in files if f.suffix == ".ipa"), None)
    if not ipa:
        raise RuntimeError("No .ipa artifact. Select the iOS target in Target Platform")
    kid, iss = os.environ.get("E2_ASC_KEY_ID"), os.environ.get("E2_ASC_ISSUER_ID")
    if not (kid and iss):
        raise RuntimeError("Set E2_ASC_KEY_ID and E2_ASC_ISSUER_ID (App Store Connect API key) before starting E2")
    await ctx.sh(key, "xcrun", "altool", "--upload-app", "-f", ipa, "-t", "ios", "--apiKey", kid, "--apiIssuer", iss)
    return f"App Store Connect / TestFlight ({ipa.name})"


async def _play(ctx, key, art, files, target, ver):
    pkg = next((f for f in files if f.suffix == ".aab"), None) or next((f for f in files if f.suffix == ".apk"), None)
    if not pkg:
        raise RuntimeError("No .aab/.apk artifact. Select the Android target in Target Platform")
    if not target:
        raise ValueError("Set the destination to the app's package name, e.g. com.example.app")
    tech.require("fastlane")
    jk = os.environ.get("E2_PLAY_JSON_KEY")
    if not jk:
        raise RuntimeError("Set E2_PLAY_JSON_KEY to the Play Console service-account JSON file")
    kind = "--aab" if pkg.suffix == ".aab" else "--apk"
    await ctx.sh(key, "fastlane", "supply", kind, pkg, "--track", "internal", "--json_key", jk, "--package_name", target)
    return f"Google Play internal track ({target})"


async def _msstore(ctx, key, art, files, target, ver):
    tech.require("msstore")
    if not target:
        raise ValueError("Set the destination to the Microsoft Store product ID")
    pkg = next((f for f in files if f.suffix.lower() in (".msix", ".msixbundle", ".msi", ".exe")), None)
    if not pkg:
        raise RuntimeError("No Windows package (.msix/.msi/.exe) among the artifacts")
    await ctx.sh(key, "msstore", "publish", pkg, "-id", target)
    return f"Microsoft Store ({target})"


DESTS = {"local": _local, "artifactory": _artifactory, "github": _github, "docker": _docker,
         "appstore": _appstore, "play": _play, "msstore": _msstore}
