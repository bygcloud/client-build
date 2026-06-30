#!/usr/bin/env python3
"""Materialize a brand's signing keystore + properties from the SIGNING_JSON env.

All brands' signing material is stored in a single CI Secret (SIGNING_JSON), a
JSON object keyed by brand id:

  { "<id>": { "keystoreB64", "alias", "storePassword", "keyPassword" }, ... }

Keeping it in one secret means no brand ids appear in the workflow files.
This script never prints secret values.

Usage:
  python scripts/signing.py --brand <id> --keystore-out <path> \
      --format {flclash|cmfa} --props-out <path>
Env:
  SIGNING_JSON  (required)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--keystore-out", required=True)
    ap.add_argument("--props-out", required=True)
    ap.add_argument("--format", required=True, choices=["flclash", "cmfa"])
    args = ap.parse_args()

    raw = os.environ.get("SIGNING_JSON", "").strip()
    if not raw:
        return "[fail] SIGNING_JSON env is empty"
    try:
        table = json.loads(raw)
    except json.JSONDecodeError as e:
        return f"[fail] SIGNING_JSON is not valid JSON: {e}"

    entry = table.get(args.brand)
    if not entry:
        return f"[fail] no signing entry for brand '{args.brand}'"

    ks_path = Path(args.keystore_out)
    ks_path.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(entry["keystoreB64"])
    ks_path.write_bytes(data)
    if ks_path.stat().st_size == 0:
        return f"[fail] decoded keystore is empty for '{args.brand}'"

    alias = entry["alias"]
    store_pw = entry["storePassword"]
    key_pw = entry["keyPassword"]

    props = Path(args.props_out)
    props.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "flclash":
        # appended to android/local.properties
        lines = [
            f"keyAlias={alias}",
            f"storePassword={store_pw}",
            f"keyPassword={key_pw}",
            "",
        ]
        with props.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    else:  # cmfa: written as signing.properties
        lines = [
            f"keystore.password={store_pw}",
            f"key.alias={alias}",
            f"key.password={key_pw}",
            "",
        ]
        props.write_text("\n".join(lines), encoding="utf-8")

    print(f"[ok] signing materialized for brand (keystore {len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
