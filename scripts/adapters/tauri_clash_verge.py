"""clash-verge-rev adapter (Tauri = Rust + React).

Applies:
1. Brand: productName / identifier across the 4 per-platform tauri confs.
2. Window title / tray tooltip / HTML title (upstream hardcodes "Clash Verge").
3. Disables upstream auto-update (otherwise the app pulls the upstream build and
   overwrites the branding).
4. Optional recommendation entry: a button in the settings header ButtonGroup
   that opens a configured URL via openWebUrl(url) (system browser).

Icons are generated separately via `@tauri-apps/cli icon <icon>`; this adapter
does not handle binary icons.
"""
from __future__ import annotations

import re
import zlib
from pathlib import Path

import _common as c


def _brand_slug(brand_id: str, fallback: str = "brand") -> str:
    """Lowercase alphanumeric brand token used for scheme / pipe / socket names."""
    slug = re.sub(r"[^a-z0-9]", "", (brand_id or fallback).lower())
    return slug or fallback


def _brand_port(brand_id: str) -> int:
    """Deterministic per-brand singleton port.

    Upstream hardcodes a single port (33331) for its singleton check. Every
    brand sharing that port means the second app to launch (or a co-installed
    official build) sees the port busy, assumes "already running", and silently
    exits -> "installs but won't launch". Derive a stable, unique high port per
    brand instead. Range 40000-49999 avoids the upstream default and ephemeral
    ranges.
    """
    return 40000 + (zlib.crc32(brand_id.encode()) % 10000)


def apply(client_dir: Path, cfg: dict, dry_run: bool = False) -> None:
    brand = cfg["brand"]
    rec = cfg.get("recommend", {})
    brand_id = cfg.get("id", "")
    app_name = brand["appName"]               # display name (window/tray/title)
    # productName drives the bundle filenames; non-ASCII chars get stripped by
    # some release tooling, yielding broken names like "_2.5.2_x64.dmg". Keep
    # productName ASCII and override the runtime window/tray/HTML title below.
    bundle_name = brand.get("appNameEn") or app_name

    # 1) productName / identifier
    #    Tauri supports per-platform config overrides (tauri.<platform>.conf.json);
    #    macOS/windows/linux confs override the base, so replace all of them or the
    #    bundle name stays the upstream one.
    conf_files = [
        "src-tauri/tauri.conf.json",
        "src-tauri/tauri.macos.conf.json",
        "src-tauri/tauri.windows.conf.json",
        "src-tauri/tauri.linux.conf.json",
    ]
    for rel in conf_files:
        conf = client_dir / rel
        is_base = rel.endswith("tauri.conf.json")
        c.regex_replace(
            conf,
            r'"productName": "[^"]*"',
            f'"productName": "{bundle_name}"',
            dry_run, required=is_base,
        )
        # identifier must be ASCII / no spaces; use packageId
        c.regex_replace(
            conf,
            r'"identifier": "[^"]*"',
            f'"identifier": "{brand["packageId"]}"',
            dry_run, required=False,
        )

    # 1b) Per-brand isolation so brands (and a co-installed official build) don't
    #     collide. Two hardcoded upstream values must be made unique per brand:
    #       - SINGLETON_SERVER port: a busy port makes the app silently exit on
    #         launch (it thinks another instance owns it). THIS is the usual
    #         "installs but won't launch" cause.
    #       - APP_ID: the per-user data dir name; sharing it mixes brand configs.
    pkg_id = brand["packageId"]
    port = _brand_port(brand_id or pkg_id)
    brand_slug = _brand_slug(brand_id or brand.get("appNameEn", "") or pkg_id)
    constants = client_dir / "src-tauri/src/constants.rs"
    # Replace the release SINGLETON_SERVER (33331). The dev one (11233) is behind
    # cfg(debug_assertions) and never built in CI, so only swap the first/real one.
    c.regex_replace(
        constants,
        r"pub const SINGLETON_SERVER: u16 = \d+;",
        f"pub const SINGLETON_SERVER: u16 = {port};",
        dry_run, count=1, required=False,
    )
    dirs_rs = client_dir / "src-tauri/src/utils/dirs.rs"
    c.regex_replace(
        dirs_rs,
        r'pub static APP_ID: &str = "[^"]*";',
        f'pub static APP_ID: &str = "{pkg_id}";',
        dry_run, count=1, required=False,
    )
    # Also isolate the backup dir name (defaults to clash-verge-rev-backup).
    c.regex_replace(
        dirs_rs,
        r'pub static BACKUP_DIR: &str = "clash-verge-rev-backup";',
        f'pub static BACKUP_DIR: &str = "{brand_slug}-backup";',
        dry_run, count=1, required=False,
    )

    # 1b-ii) The mihomo core IPC channel is ALSO hardcoded to the upstream name.
    #        On Windows it is a named pipe (\\.\pipe\verge-mihomo); on unix a
    #        socket file (verge-mihomo.sock). A co-installed official build owns
    #        the same pipe/socket, so the branded core can't bind its own channel
    #        and the app "runs but the core won't connect / proxy toggle dead".
    #        Make the channel name per-brand too.
    c.regex_replace(
        dirs_rs,
        r'\\\\\.\\pipe\\verge-mihomo',
        rf'\\\\.\\pipe\\{brand_slug}-mihomo',
        dry_run, count=0, required=False,
    )
    c.regex_replace(
        dirs_rs,
        r'"verge-mihomo\.sock"',
        f'"{brand_slug}-mihomo.sock"',
        dry_run, count=0, required=False,
    )

    # 1b-iii) The external-controller default (127.0.0.1:9097) is hardcoded in
    #         constants.rs plus two runtime fallbacks in config/clash.rs. Sharing
    #         the TCP controller port with an official build is another collision
    #         source when the core runs in TCP (non-IPC) mode. Use a per-brand
    #         port. Test-only 9097 assertions are left untouched (build-inert).
    ext_ctrl_port = 9100 + (zlib.crc32((brand_id or pkg_id).encode()) % 700)
    c.regex_replace(
        constants,
        r'pub const DEFAULT_EXTERNAL_CONTROLLER: &str = "127\.0\.0\.1:\d+";',
        f'pub const DEFAULT_EXTERNAL_CONTROLLER: &str = "127.0.0.1:{ext_ctrl_port}";',
        dry_run, count=1, required=False,
    )
    clash_rs = client_dir / "src-tauri/src/config/clash.rs"
    c.regex_replace(
        clash_rs,
        r'\.unwrap_or_else\(\|\| "127\.0\.0\.1:9097"\.into\(\)\)',
        f'.unwrap_or_else(|| "127.0.0.1:{ext_ctrl_port}".into())',
        dry_run, count=0, required=False,
    )
    c.regex_replace(
        clash_rs,
        r'Err\(_\) => "127\.0\.0\.1:9097"\.into\(\),',
        f'Err(_) => "127.0.0.1:{ext_ctrl_port}".into(),',
        dry_run, count=0, required=False,
    )

    # 1b-iv) deep-link schemes. Upstream registers ["clash", "clash-verge"].
    #        Keep "clash" (the site tutorials' one-click import emits
    #        clash://install-config?url=..., which must still resolve to the app)
    #        and add a brand-specific scheme for future deterministic routing.
    #        Only the base conf carries the deep-link block.
    c.regex_replace(
        client_dir / "src-tauri/tauri.conf.json",
        r'"schemes":\s*\[[^\]]*\]',
        f'"schemes": ["clash", "clash-verge", "{brand_slug}"]',
        dry_run, count=0, required=False,
    )

    # 1c) macOS: force a proper ad-hoc signature. Upstream leaves
    #     signingIdentity=null, so `tauri build` only emits the linker-level
    #     ad-hoc Mach-O signature and never seals the .app's CodeResources. On
    #     Apple Silicon, Gatekeeper rejects such a bundle ("is damaged / can't be
    #     opened") and the app won't launch. Setting "-" makes Tauri run
    #     `codesign --force --deep --sign -`, sealing the bundle so it launches.
    macos_conf = client_dir / "src-tauri/tauri.macos.conf.json"
    if not c.regex_replace(
        macos_conf,
        r'"signingIdentity":\s*null',
        '"signingIdentity": "-"',
        dry_run, count=1, required=False,
    ):
        # No signingIdentity key present: add one inside bundle.macOS.
        c.regex_replace(
            macos_conf,
            r'("macOS"\s*:\s*\{)',
            r'\1\n      "signingIdentity": "-",',
            dry_run, count=1, required=False,
        )

    # 2) Window title / tray tooltip / HTML title (upstream hardcodes "Clash Verge").
    #    Leave Cargo.toml name=clash-verge alone (keep the Linux binary name ASCII).
    c.replace_once(
        client_dir / "src/index.html",
        "<title>Clash Verge</title>",
        f"<title>{app_name}</title>",
        dry_run, required=False,
    )
    c.replace_once(
        client_dir / "src-tauri/src/utils/resolve/window.rs",
        '.title("Clash Verge")',
        f'.title("{app_name}")',
        dry_run, required=False,
    )
    c.replace_once(
        client_dir / "src-tauri/src/lib.rs",
        'window.set_title("Clash Verge")',
        f'window.set_title("{app_name}")',
        dry_run, required=False,
    )
    c.replace_once(
        client_dir / "src-tauri/src/core/tray/mod.rs",
        '"Clash Verge {}\\n{}: {}\\n{}: {}\\n{}: {}"',
        f'"{app_name} {{}}\\n{{}}: {{}}\\n{{}}: {{}}\\n{{}}: {{}}"',
        dry_run, required=False,
    )

    # 3) Disable upstream auto-update:
    #    a) createUpdaterArtifacts=false — otherwise bundling needs a signing key;
    #    b) remove updater.pubkey — a pubkey also forces signing;
    #    c) point endpoints at an own repo (missing -> silently no update) so the
    #       upstream latest.json can't replace the build with an unbranded one.
    base_conf = client_dir / "src-tauri/tauri.conf.json"
    c.regex_replace(
        base_conf,
        r'"createUpdaterArtifacts":\s*(true|false)',
        '"createUpdaterArtifacts": false',
        dry_run, required=False,
    )
    # Drop pubkey together with its trailing comma to avoid leaving broken JSON
    # like "updater": {,
    c.regex_replace(
        base_conf,
        r'"pubkey":\s*"[^"]*"\s*,?\s*',
        "",
        dry_run, required=False,
    )
    upd_repo = brand.get("updaterRepo")  # optional, e.g. "<owner>/<repo>"
    if upd_repo:
        c.regex_replace(
            base_conf,
            r'"endpoints":\s*\[[^\]]*\]',
            '"endpoints": [\n      "https://github.com/'
            + upd_repo
            + '/releases/download/updater/update.json"\n    ]',
            dry_run, required=False,
        )

    # 4) Inject the recommendation button into the settings header
    if rec.get("enabled", False) and rec.get("purchaseUrl"):
        settings = client_dir / "src/pages/settings.tsx"
        title = rec.get("title", "")

        # import the Redeem icon
        c.replace_once(
            settings,
            "import { GitHub, HelpOutlineRounded, Telegram } from '@mui/icons-material'",
            "import { GitHub, HelpOutlineRounded, Telegram, RedeemRounded } from '@mui/icons-material'",
            dry_run, required=False,
        )

        # add the click handler
        handler = (
            "\n  const toRecommend = useLockFn(() => {\n"
            f"    return openWebUrl('{rec['purchaseUrl']}')\n"
            "  })\n"
        )
        c.insert_after(
            settings,
            "  const toTelegramChannel = useLockFn(() => {\n"
            "    return openWebUrl('https://t.me/clash_verge_re')\n"
            "  })\n",
            handler,
            dry_run,
        )

        # insert the button at the top of the ButtonGroup
        button = (
            "\n          <IconButton\n"
            "            size=\"medium\"\n"
            "            color=\"inherit\"\n"
            f"            title=\"{title}\"\n"
            "            onClick={toRecommend}\n"
            "          >\n"
            "            <RedeemRounded fontSize=\"inherit\" />\n"
            "          </IconButton>"
        )
        c.insert_after(
            settings,
            '<ButtonGroup variant="contained" aria-label="Basic button group">',
            button,
            dry_run,
        )

    c.log("clash-verge-rev adapter applied")
