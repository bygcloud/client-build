#!/usr/bin/env python3
"""Upload built client artifacts straight to object storage (R2) from CI.

Artifacts are published to storage only — never to this repo's Releases — so the
public repo carries no brand-identifying binaries. Keys are stable and version
agnostic so downstream config never needs to change.

  desktop:  clients/<brand>/<Brand>_<arch>.<ext>
  flclash:  clients/<brand>/<Brand>-android-<abi>.apk
  cmfa:     clients/<brand>/<Brand>-cmfa-<abi>.apk

Usage:
  python scripts/r2_upload.py --brand <id> --name <Brand> --kind desktop \
      --glob 'src/src-tauri/target/**/release/bundle/**/*'
Env required:
  R2_ACCOUNT_ID, R2_BUCKET, R2_AUTH_EMAIL, R2_AUTH_KEY
"""
from __future__ import annotations

import argparse
import glob as globmod
import os
import re
import sys
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


def r2_put(data: bytes, key: str, ct: str) -> bool:
    acct = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ["R2_BUCKET"]
    url = (f"https://api.cloudflare.com/client/v4/accounts/{acct}"
           f"/r2/buckets/{bucket}/objects/{key}")
    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "X-Auth-Email": os.environ["R2_AUTH_EMAIL"],
        "X-Auth-Key": os.environ["R2_AUTH_KEY"],
        "Content-Type": ct,
    })
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}: {e.read()[:160].decode(errors='ignore')}")
        return False


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
            else:
                print("FAIL")
                return f"[fail] upload {p.name}"

    if uploaded == 0:
        return f"[fail] no artifacts matched for kind={args.kind}"
    print(f"[ok] uploaded {uploaded} artifact(s) for {args.name}/{args.kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
