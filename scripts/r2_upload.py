#!/usr/bin/env python3
"""Upload built client artifacts straight to object storage (R2) from CI.

Artifacts are published to storage only — never to this repo's Releases — so the
public repo carries no brand-identifying binaries. Keys are stable and version
agnostic so downstream config never needs to change.

  desktop:  clients/<brand>/<Brand>_<arch>.<ext>
  flclash:  clients/<brand>/<Brand>-android-<abi>.apk
  cmfa:     clients/<brand>/<Brand>-cmfa-<abi>.apk

The keys are STABLE (no version in the path), so the public download URL never
changes. That is convenient for downstream config, but it means the object at a
given URL is overwritten in place on every build. Cloudflare's CDN caches these
objects at the edge, so overwriting the R2 origin is NOT enough: the edge keeps
serving the old copy until its TTL expires (observed: an .exe frozen for 5.5
days on cf-cache-status: HIT while every rebuild silently updated only R2). To
make a rebuild actually reach customers we must, on every upload:

  1. set a short Cache-Control (max-age=300) so staleness is bounded even if a
     purge is missed, and
  2. explicitly purge the exact public URL from the Cloudflare edge cache, and
  3. publish a VERSION.json alongside the binaries recording the git commit +
     build time, so the live package can always be traced to a build/commit.

Usage:
  python scripts/r2_upload.py --brand <id> --name <Brand> --kind desktop \
      --glob 'src/src-tauri/target/**/release/bundle/**/*'
Env required:
  R2_ACCOUNT_ID, R2_BUCKET, R2_AUTH_EMAIL, R2_AUTH_KEY
Env optional:
  PUBLIC_BASE   public download base URL (default https://store.fastrb.com)
  CF_ZONE       Cloudflare zone name for cache purge (default fastrb.com)
  GITHUB_SHA / GITHUB_RUN_ID / GITHUB_REF_NAME  recorded into VERSION.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob as globmod
import json
import os
import re
import urllib.request
import urllib.error
from pathlib import Path

KEEP_EXT = {"exe", "dmg", "deb", "rpm", "apk"}
MIME = {
    "apk": "application/vnd.android.package-archive",
    "exe": "application/x-msdownload",
    "dmg": "application/x-apple-diskimage",
    "deb": "application/vnd.debian.binary-package",
    "rpm": "application/x-rpm",
}

# Short edge TTL: even if a purge is ever skipped/fails, a stale binary can only
# survive 5 minutes instead of freezing for days.
CACHE_CONTROL = "public, max-age=300"

PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://store.fastrb.com").rstrip("/")
CF_ZONE = os.environ.get("CF_ZONE", "fastrb.com")
CF_API = "https://api.cloudflare.com/client/v4"


def _cf_headers() -> dict:
    return {
        "X-Auth-Email": os.environ["R2_AUTH_EMAIL"],
        "X-Auth-Key": os.environ["R2_AUTH_KEY"],
        "Content-Type": "application/json",
    }


def r2_put(data: bytes, key: str, ct: str) -> bool:
    acct = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ["R2_BUCKET"]
    url = (f"{CF_API}/accounts/{acct}"
           f"/r2/buckets/{bucket}/objects/{key}")
    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "X-Auth-Email": os.environ["R2_AUTH_EMAIL"],
        "X-Auth-Key": os.environ["R2_AUTH_KEY"],
        "Content-Type": ct,
        # Bound edge staleness; the explicit purge below is the primary fix.
        "Cache-Control": CACHE_CONTROL,
    })
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}: {e.read()[:160].decode(errors='ignore')}")
        return False


def cf_zone_id(zone: str) -> str | None:
    """Resolve a Cloudflare zone name to its id (needed for cache purge)."""
    url = f"{CF_API}/zones?name={zone}&status=active"
    req = urllib.request.Request(url, headers=_cf_headers())
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"    zone lookup HTTP {e.code}: {e.read()[:160].decode(errors='ignore')}")
        return None
    results = body.get("result") or []
    return results[0]["id"] if results else None


def cf_purge(urls: list[str]) -> bool:
    """Purge specific URLs from the Cloudflare edge cache (batches of 30)."""
    if not urls:
        return True
    zid = cf_zone_id(CF_ZONE)
    if not zid:
        print(f"    [warn] could not resolve zone {CF_ZONE}; edge cache NOT purged")
        return False
    ok = True
    for i in range(0, len(urls), 30):
        batch = urls[i:i + 30]
        payload = json.dumps({"files": batch}).encode()
        url = f"{CF_API}/zones/{zid}/purge_cache"
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers=_cf_headers())
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode())
                if not body.get("success"):
                    print(f"    purge errors: {body.get('errors')}")
                    ok = False
        except urllib.error.HTTPError as e:
            print(f"    purge HTTP {e.code}: {e.read()[:160].decode(errors='ignore')}")
            ok = False
    return ok


def stable_key(brand: str, bid: str, filename: str) -> str:
    name = re.sub(r"[_-]\d+\.\d+\.\d+(?:[._-]\d+)?", "", filename)
    name = re.sub(r"^FlClash[-_]?", f"{brand}-", name)
    name = re.sub(r"^cmfa[-_]?", f"{brand}-cmfa-", name, flags=re.I)
    name = re.sub(r"[-_]{2,}", "-", name).strip("-_")
    if not name.lower().startswith(brand.lower()):
        name = f"{brand}-{name}"
    return f"clients/{bid}/{name}"


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True, help="brand id (storage path)")
    ap.add_argument("--name", required=True, help="Brand display name (file prefix)")
    ap.add_argument("--kind", required=True, choices=["desktop", "flclash", "cmfa"])
    ap.add_argument("--glob", required=True, action="append",
                    help="glob(s) of files to upload (recursive)")
    args = ap.parse_args()

    seen: set[str] = set()
    uploaded = 0
    keys: list[str] = []
    for g in args.glob:
        for fp in globmod.glob(g, recursive=True):
            p = Path(fp)
            if not p.is_file():
                continue
            ext = p.suffix.lstrip(".").lower()
            if ext not in KEEP_EXT:
                continue
            if p.name in seen:
                continue
            seen.add(p.name)
            key = stable_key(args.name, args.brand, p.name)
            data = p.read_bytes()
            print(f"  up {p.name} ({human(len(data))}) -> {key} ... ", end="", flush=True)
            if r2_put(data, key, MIME.get(ext, "application/octet-stream")):
                print("ok")
                uploaded += 1
                keys.append(key)
            else:
                print("FAIL")
                return f"[fail] upload {p.name}"

    if uploaded == 0:
        return f"[fail] no artifacts matched for kind={args.kind}"

    # Version stamp: record which build/commit produced these binaries so the
    # live package can always be traced back (answers "which commit is live?").
    sha = os.environ.get("GITHUB_SHA", "")
    version = {
        "brand": args.brand,
        "kind": args.kind,
        "commit": sha,
        "commit_short": sha[:8],
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "ref": os.environ.get("GITHUB_REF_NAME", ""),
        "built_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": [Path(k).name for k in keys],
    }
    vkey = f"clients/{args.brand}/VERSION.{args.kind}.json"
    vdata = json.dumps(version, ensure_ascii=False, indent=2).encode()
    print(f"  up VERSION.{args.kind}.json -> {vkey} ... ", end="", flush=True)
    if r2_put(vdata, vkey, "application/json"):
        print("ok")
        keys.append(vkey)
    else:
        print("FAIL (non-fatal)")

    # CRITICAL: overwriting R2 is not enough — purge the Cloudflare edge cache for
    # the exact public URLs, otherwise customers keep downloading the cached old
    # build (observed frozen for days on cf-cache-status: HIT).
    purge_urls = [f"{PUBLIC_BASE}/{k}" for k in keys]
    print(f"  purging {len(purge_urls)} edge URL(s) on zone {CF_ZONE} ...")
    for u in purge_urls:
        print(f"    - {u}")
    if cf_purge(purge_urls):
        print("  [ok] edge cache purged")
    else:
        print("  [warn] edge purge failed; short Cache-Control still bounds staleness to 5m")

    print(f"[ok] uploaded {uploaded} artifact(s) for {args.name}/{args.kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
