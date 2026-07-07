#!/usr/bin/env python3
"""One-shot Cloudflare edge-cache purge for published client artifacts.

Why this exists: artifacts are uploaded to R2 under STABLE, version-agnostic
keys, so the public download URL never changes and is overwritten in place on
every build. Cloudflare caches those objects at the edge; overwriting R2 does
NOT evict the edge copy. A stale binary was observed frozen at the edge for
5.5 days (cf-cache-status: HIT, age ~477000s) while every rebuild only updated
R2 — so customers kept downloading an old build no matter how many times we
rebuilt. r2_upload.py now purges on every upload, but this standalone script
lets us purge on demand (e.g. to unstick already-cached URLs immediately,
without waiting for a rebuild).

It enumerates the objects actually present in the bucket (authoritative — no
filename guessing) and purges their public URLs from the edge.

Env required:
  R2_ACCOUNT_ID, R2_BUCKET, R2_AUTH_EMAIL, R2_AUTH_KEY
Env optional:
  PUBLIC_BASE   public download base URL (default https://store.fastrb.com)
  CF_ZONE       Cloudflare zone name for purge (default fastrb.com)
  PREFIX        only purge keys under this prefix (default clients/)
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import urllib.error

CF_API = "https://api.cloudflare.com/client/v4"
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://store.fastrb.com").rstrip("/")
CF_ZONE = os.environ.get("CF_ZONE", "fastrb.com")
PREFIX = os.environ.get("PREFIX", "clients/")


def _headers(ct: str | None = None) -> dict:
    h = {
        "X-Auth-Email": os.environ["R2_AUTH_EMAIL"],
        "X-Auth-Key": os.environ["R2_AUTH_KEY"],
    }
    if ct:
        h["Content-Type"] = ct
    return h


def list_keys(prefix: str) -> list[str]:
    acct = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ["R2_BUCKET"]
    keys: list[str] = []
    cursor = ""
    while True:
        q = {"prefix": prefix, "per_page": "1000"}
        if cursor:
            q["cursor"] = cursor
        url = (f"{CF_API}/accounts/{acct}/r2/buckets/{bucket}/objects?"
               + urllib.parse.urlencode(q))
        req = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
        for obj in body.get("result") or []:
            keys.append(obj["key"])
        info = body.get("result_info") or {}
        cursor = info.get("cursor") or ""
        if not cursor:
            break
    return keys


def zone_id(zone: str) -> str | None:
    url = f"{CF_API}/zones?name={zone}&status=active"
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode())
    r = body.get("result") or []
    return r[0]["id"] if r else None


def purge(urls: list[str]) -> bool:
    zid = zone_id(CF_ZONE)
    if not zid:
        print(f"[fail] cannot resolve zone {CF_ZONE}")
        return False
    ok = True
    for i in range(0, len(urls), 30):
        batch = urls[i:i + 30]
        payload = json.dumps({"files": batch}).encode()
        url = f"{CF_API}/zones/{zid}/purge_cache"
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers=_headers("application/json"))
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode())
                if body.get("success"):
                    print(f"  purged batch {i//30 + 1}: {len(batch)} url(s)")
                else:
                    print(f"  batch {i//30 + 1} errors: {body.get('errors')}")
                    ok = False
        except urllib.error.HTTPError as e:
            print(f"  batch {i//30 + 1} HTTP {e.code}: {e.read()[:160].decode(errors='ignore')}")
            ok = False
    return ok


def main() -> int:
    keys = list_keys(PREFIX)
    if not keys:
        print(f"[warn] no objects under prefix {PREFIX!r}")
        return 0
    urls = [f"{PUBLIC_BASE}/{k}" for k in keys]
    print(f"purging {len(urls)} edge URL(s) under {PREFIX!r} on zone {CF_ZONE}:")
    for u in urls:
        print(f"  - {u}")
    return 0 if purge(urls) else "[fail] one or more purge batches failed"


if __name__ == "__main__":
    raise SystemExit(main())
