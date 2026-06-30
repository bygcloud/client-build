#!/usr/bin/env python3
"""Fetch a brand's build config + icon from object storage (R2) at CI time.

Brand data is intentionally NOT stored in this repository. CI passes an opaque
brand id and the storage credentials (as Secrets); this script pulls the brand's
config and icon, writes the icon to a local file, and emits the resolved config
so the build step can consume it via the BRAND_JSON env var.

Outputs (written to --out-dir, default current dir):
  brand.json   resolved config (iconPath points at the local icon)
  icon.png     the brand icon

Usage:
  python scripts/fetch_brand.py --brand <id> --out-dir .
Env required:
  R2_ACCOUNT_ID, R2_BUCKET, R2_AUTH_EMAIL, R2_AUTH_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path


def r2_get(key: str) -> bytes:
    acct = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ["R2_BUCKET"]
    url = (f"https://api.cloudflare.com/client/v4/accounts/{acct}"
           f"/r2/buckets/{bucket}/objects/{key}")
    req = urllib.request.Request(url, headers={
        "X-Auth-Email": os.environ["R2_AUTH_EMAIL"],
        "X-Auth-Key": os.environ["R2_AUTH_KEY"],
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    try:
        cfg = json.loads(r2_get(f"brands/{args.brand}/brand.json"))
        icon = r2_get(f"brands/{args.brand}/icon.png")
    except urllib.error.HTTPError as e:
        return f"[fail] fetch brand '{args.brand}': {e.code}"

    icon_path = out / "icon.png"
    icon_path.write_bytes(icon)

    cfg.setdefault("id", args.brand)
    cfg.setdefault("brand", {})
    cfg["brand"]["iconPath"] = str(icon_path)
    cfg["brand"].pop("iconUrl", None)
    cfg["brand"].pop("iconB64", None)

    (out / "brand.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    # brand name only (safe-ish) to stdout for the workflow to read if needed
    print(cfg["brand"].get("appNameEn", args.brand))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
