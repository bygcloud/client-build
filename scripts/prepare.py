#!/usr/bin/env python3
"""CI build preparation: clone upstream, apply a profile, generate icons.

Runs inside GitHub Actions for one (profile, client) pair. Steps are
cross-platform (Linux/macOS/Windows runners):

  clone upstream -> apply profile adapter -> generate icons
  -> (mobile) embed submodules so the build is self-contained

Profile data and signing material are NEVER in this repo; CI injects the
profile via the BRAND_JSON env var and signing keys from Secrets.

Usage:
  python scripts/prepare.py --profile <id> --client clash-verge-rev --out ./src
  python scripts/prepare.py --profile <id> --client FlClash --out ./src
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"
ADAPTERS = ROOT / "scripts" / "adapters"

UPSTREAM = {
    "clash-verge-rev": ("https://github.com/clash-verge-rev/clash-verge-rev.git", True),
    "FlClash": ("https://github.com/chen08209/FlClash.git", False),
    "ClashMetaForAndroid": ("https://github.com/MetaCubeX/ClashMetaForAndroid.git", False),
}
FLCLASH_SUBMODULES = [
    ("core/Clash.Meta", "https://github.com/chen08209/Clash.Meta.git", "FlClash"),
    ("plugins/flutter_distributor", "https://github.com/chen08209/flutter_distributor.git", "FlClash"),
    ("plugins/tray_manager", "https://github.com/chen08209/tray_manager.git", "main"),
]
CMFA_SUBMODULES = [
    ("core/src/foss/golang/clash", "https://github.com/MetaCubeX/mihomo.git", "Alpha"),
]


def sh(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), text=True)
    if check and r.returncode != 0:
        sys.exit(f"[fail] {cmd}")
    return r


def load_profile(pid):
    """Load a build profile. Profile data is NOT stored in this repo. CI injects
    a single profile's JSON via the BRAND_JSON env var (sourced from a Secret);
    the icon rides along as base64 in brand.iconB64 and is materialized to a temp
    file. Falls back to a local profiles/<id>.json file only for offline
    development (that dir is git-ignored and never published)."""
    raw = os.environ.get("BRAND_JSON", "").strip()
    if raw:
        cfg = json.loads(raw)
    else:
        p = PROFILES / f"{pid}.json"
        if not p.exists():
            sys.exit(f"[fail] no BRAND_JSON env and no local {p}")
        cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("id", pid)
    _materialize_icon(cfg)
    return cfg


def _materialize_icon(cfg):
    """If the brand carries a base64 icon (CI/secret path), write it to a temp
    PNG and point iconPath at it. Otherwise leave iconPath as a repo-relative
    path (local dev only)."""
    import base64
    brand = cfg.get("brand", {})
    b64 = brand.get("iconB64")
    if b64:
        data = base64.b64decode(b64)
        tmp = Path(tempfile.mktemp(suffix=".png"))
        tmp.write_bytes(data)
        brand["iconPath"] = str(tmp)
        brand.pop("iconB64", None)
    elif brand.get("iconPath") and not os.path.isabs(brand["iconPath"]):
        brand["iconPath"] = str((ROOT / brand["iconPath"]).resolve())


def run_adapter(client, src_dir, cfg):
    sys.path.insert(0, str(ADAPTERS))
    mod = {
        "clash-verge-rev": "tauri_clash_verge",
        "FlClash": "flutter_flclash",
        "ClashMetaForAndroid": "android_cmfa",
    }[client]
    importlib.import_module(mod).apply(src_dir, cfg, dry_run=False)


def gen_icon_verge(src_dir, icon_path):
    # @tauri-apps/cli is cross-platform (Node). Generates all platform icons.
    # On Windows, npx is npx.cmd which Python's list-form subprocess can't resolve
    # (it only finds .exe); run as a shell string so the .cmd is found.
    npx = "npx.cmd" if os.name == "nt" else "npx"
    sh(f'{npx} --yes @tauri-apps/cli icon "{icon_path}"', cwd=str(src_dir))


def _resize_png(src, dst, px):
    """Resize using PIL (portable). Returns dst."""
    from PIL import Image
    im = Image.open(src).convert("RGBA").resize((px, px), Image.LANCZOS)
    im.save(dst)
    return dst


def gen_icon_flclash(src_dir, icon_path, primary_hex):
    """Generate Android launcher icons (webp) at all densities, portable."""
    import re
    res = src_dir / "android/app/src/main/res"
    legacy = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    adaptive = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}

    def to_webp(png, webp):
        # cwebp is available on ubuntu runners (webp pkg); fall back to PIL.
        if subprocess.run(["cwebp", "-quiet", "-q", "90", str(png), "-o", str(webp)],
                          capture_output=True).returncode != 0:
            from PIL import Image
            Image.open(png).save(webp, "WEBP", quality=90)

    for dens, px in legacy.items():
        tmp = Path(tempfile.mktemp(suffix=".png"))
        _resize_png(icon_path, tmp, px)
        for name in ("ic_launcher.webp", "ic_launcher_round.webp"):
            (res / f"mipmap-{dens}").mkdir(parents=True, exist_ok=True)
            to_webp(tmp, res / f"mipmap-{dens}/{name}")
    for dens, px in adaptive.items():
        tmp = Path(tempfile.mktemp(suffix=".png"))
        _resize_png(icon_path, tmp, px)
        to_webp(tmp, res / f"mipmap-{dens}/ic_launcher_foreground.webp")
    for xml in ("ic_launcher.xml", "ic_launcher_round.xml"):
        p = res / f"mipmap-anydpi-v26/{xml}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <background android:drawable="@color/ic_launcher_background"/>\n'
            '    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>\n'
            '</adaptive-icon>', encoding="utf-8")
    bg = res / "values/ic_launcher_background.xml"
    if bg.exists():
        bg.write_text(re.sub(r'name="ic_launcher_background">#[0-9A-Fa-f]+<',
                             f'name="ic_launcher_background">{primary_hex.upper()}<',
                             bg.read_text()), encoding="utf-8")


def embed_flclash_submodules(src_dir):
    for path, url, branch in FLCLASH_SUBMODULES:
        d = src_dir / path
        sh(f'rm -rf "{d}"')
        sh(["git", "clone", "--depth", "1", "-b", branch, url, str(d)])
        sh(f'rm -rf "{d / ".git"}"')
    gm = src_dir / ".gitmodules"
    if gm.exists():
        gm.unlink()


def embed_cmfa_submodules(src_dir):
    # CMFA's Go core (mihomo) is a submodule pinned to a SPECIFIC commit. Cloning
    # the branch tip causes go.mod mismatches (e.g. tun.StackOptions.ICMPTimeout),
    # so check out the exact pinned commit via real submodule init.
    sh(["git", "submodule", "update", "--init", "--recursive", "--force"], cwd=str(src_dir))


def patch_cmfa_cmake(src_dir):
    """CMFA's core CMakeLists derives a version string from
    `git submodule foreach git branch -r --contains <hash>`. Shallow submodule
    clones have no remote-tracking refs, so CURRENT_BRANCH is empty and CMake
    crashes (string REGEX on empty). Replace the fragile branch lookup with a
    fixed label — the value is only embedded as cosmetic version metadata.
    """
    import re
    cml = src_dir / "core/src/main/cpp/CMakeLists.txt"
    if not cml.exists():
        return
    txt = cml.read_text(encoding="utf-8")
    # Replace the whole branch-detection execute_process + string ops block with a
    # static assignment. Match from the branch execute_process up to the
    # message(STATUS "git current branch ...") line.
    txt = re.sub(
        r'execute_process\(\s*COMMAND git submodule foreach git branch.*?message\(STATUS "git current branch = \$\{CURRENT_BRANCH\}"\)',
        'set(CURRENT_BRANCH "release")\nmessage(STATUS "git current branch = ${CURRENT_BRANCH}")',
        txt, flags=re.S)
    cml.write_text(txt, encoding="utf-8")
    print("  patched CMFA CMakeLists branch detection")


def gen_icon_cmfa(src_dir, icon_path, primary_hex):
    """CMFA launcher icons: PNG mipmaps (ic_launcher / ic_launcher_round) at all
    densities + adaptive foreground drawable + brand background color."""
    import re
    res = src_dir / "app/src/main/res"
    legacy = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    # adaptive foreground lives in drawable-* at 108dp-equivalent densities
    fg = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}

    for dens, px in legacy.items():
        d = res / f"mipmap-{dens}"
        d.mkdir(parents=True, exist_ok=True)
        for name in ("ic_launcher.png", "ic_launcher_round.png"):
            _resize_png(icon_path, d / name, px)
    for dens, px in fg.items():
        d = res / f"drawable-{dens}"
        d.mkdir(parents=True, exist_ok=True)
        _resize_png(icon_path, d / "ic_launcher_foreground.png", px)

    # adaptive icon XML -> bitmap foreground + brand bg color (drop monochrome ref
    # that points at a vector we replaced with a bitmap)
    for xml in ("ic_launcher.xml", "ic_launcher_round.xml"):
        p = res / f"mipmap-anydpi-v26/{xml}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <background android:drawable="@color/ic_launcher_background"/>\n'
            '    <foreground android:drawable="@drawable/ic_launcher_foreground"/>\n'
            '</adaptive-icon>', encoding="utf-8")
    # remove any vector foreground that would clash with our bitmap drawable
    for vec in (res / "drawable/ic_launcher_foreground.xml",
                res / "drawable-anydpi-v24/ic_launcher_foreground.xml",
                res / "drawable-v24/ic_launcher_foreground.xml"):
        if vec.exists():
            vec.unlink()
    # set brand background color
    bg = res / "values/ic_launcher_background.xml"
    if bg.exists():
        bg.write_text(re.sub(r'(name="ic_launcher_background">)#[0-9A-Fa-f]+(<)',
                             rf'\g<1>{primary_hex.upper()}\g<2>',
                             bg.read_text()), encoding="utf-8")
    else:
        bg.parent.mkdir(parents=True, exist_ok=True)
        bg.write_text('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                      f'    <color name="ic_launcher_background">{primary_hex.upper()}</color>\n'
                      '</resources>\n', encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", dest="profile", required=True)
    ap.add_argument("--client", required=True,
                    choices=["clash-verge-rev", "FlClash", "ClashMetaForAndroid"])
    ap.add_argument("--tag", default=None, help="upstream tag/branch to clone (verge needs it)")
    ap.add_argument("--out", default="./src", help="where to place prepared source")
    args = ap.parse_args()

    cfg = load_profile(args.profile)
    brand = cfg["brand"]
    icon_path = Path(brand["iconPath"])  # already resolved by load_profile
    out = Path(args.out).resolve()
    url, needs_tag = UPSTREAM[args.client]

    print(f"::group::clone {args.client}")
    sh(f'rm -rf "{out}"')
    clone = ["git", "clone", "--depth", "1"]
    if needs_tag and args.tag:
        clone += ["-b", args.tag]
    # CMFA pins its Go core (mihomo) to an exact submodule commit; fetch it at
    # clone time so we build the matching core (branch tip breaks go.mod).
    if args.client == "ClashMetaForAndroid":
        clone += ["--recurse-submodules", "--shallow-submodules"]
    clone += [url, str(out)]
    sh(clone)
    print("::endgroup::")

    print("::group::brand")
    run_adapter(args.client, out, cfg)
    print("::endgroup::")

    print("::group::icons")
    if args.client == "clash-verge-rev":
        gen_icon_verge(out, icon_path)
    elif args.client == "FlClash":
        gen_icon_flclash(out, icon_path, brand.get("primaryColor", "#1976D2"))
    else:  # ClashMetaForAndroid
        gen_icon_cmfa(out, icon_path, brand.get("primaryColor", "#1976D2"))
    print("::endgroup::")

    if args.client == "FlClash":
        print("::group::embed core")
        embed_flclash_submodules(out)
        print("::endgroup::")
    elif args.client == "ClashMetaForAndroid":
        print("::group::embed core")
        embed_cmfa_submodules(out)
        patch_cmfa_cmake(out)
        print("::endgroup::")

    print(f"[ok] prepared {brand.get('appNameEn', args.profile)} / {args.client} at {out}")


if __name__ == "__main__":
    raise SystemExit(main())
