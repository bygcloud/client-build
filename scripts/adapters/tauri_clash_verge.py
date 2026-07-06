"""clash-verge-rev adapter (Tauri = Rust + React).

Applies:
1. Brand: productName / identifier across the 4 per-platform tauri confs.
2. Window title / tray tooltip / HTML title (upstream hardcodes "Clash Verge").
3. Disables upstream auto-update (otherwise the app pulls the upstream build and
   overwrites the branding).
4. Optional recommendation entries (both open recommend.purchaseUrl in the
   system browser via openWebUrl):
     a) a gift button in the settings header ButtonGroup, and
     b) a prominent full-width banner card at the top of the home page.
5. Pins the mihomo (verge-mihomo) core version in scripts/prebuild.mjs so the
   bundled tauri-plugin-mihomo can deserialize the core's API responses.
6. Rebrands the clash-verge-i18n crate's notification/tray/service strings
   (separate from src/locales) across all shipped languages.

Icons are generated separately via `@tauri-apps/cli icon <icon>`; this adapter
does not handle binary icons.
"""
from __future__ import annotations

import re
import zlib
from pathlib import Path

import _common as c

# mihomo (verge-mihomo) stable core pinned for the desktop build. Upstream's
# scripts/prebuild.mjs otherwise grabs the LATEST stable at build time, which
# can be newer than what upstream v2.5.1's tauri-plugin-mihomo can strictly
# parse, breaking the UI's core communication (empty proxy groups / current
# node and "内核通信错误" on the mode card) even though the core runs and
# traffic flows. v1.19.25 (2026-05-16) is the stable from v2.5.1's release era.
MIHOMO_PINNED_VERSION = "v1.19.25"


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


# A valid (upstream) minisign public key. The updater plugin requires *a*
# pubkey to initialise; keeping this one is harmless because we never produce
# signed update artifacts and point the endpoints at a dead URL, so no update
# can ever be fetched or applied. Used only as a fallback if the upstream conf
# somehow lacks a pubkey.
_FALLBACK_PUBKEY = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEQyOEMyRjBCQkVGOUJ"
    "EREYKUldUZnZmbStDeStNMHU5Mmo1N24xQXZwSVRYbXA2NUpzZE5oVzlqeS9Bc0t6RVV4Mmt"
    "wVjBZaHgK"
)


def _neuter_updater(base_conf: Path, brand: dict, dry_run: bool) -> None:
    """Disable auto-update while keeping plugins.updater well-formed.

    Rewrites the plugins.updater block via JSON so the required `pubkey` field is
    always present (its absence crashes plugin init on macOS/Windows). Sets
    createUpdaterArtifacts=false and points endpoints at a dead brand URL so no
    real update can ever be fetched.
    """
    import json

    if not base_conf.exists():
        c.log(f"skip updater neuter (missing): {base_conf.name}")
        return
    conf = json.loads(c.read(base_conf))

    # No signed update artifacts (bundling would otherwise need a signing key).
    b = conf.setdefault("bundle", {})
    b["createUpdaterArtifacts"] = False

    plugins = conf.get("plugins")
    if isinstance(plugins, dict) and "updater" in plugins:
        upd = plugins["updater"]
        if not isinstance(upd, dict):
            upd = {}
        # Guarantee the required pubkey stays present.
        if not upd.get("pubkey"):
            upd["pubkey"] = _FALLBACK_PUBKEY
        # Dead endpoint: prefer a per-brand repo path if provided, else a URL
        # that resolves to nothing publishable. Either way we never ship a
        # manifest there, so no update is ever found.
        upd_repo = brand.get("updaterRepo")
        if upd_repo:
            upd["endpoints"] = [
                f"https://github.com/{upd_repo}/releases/download/updater/update.json"
            ]
        else:
            upd["endpoints"] = [
                "https://updates.invalid.localhost/no-update.json"
            ]
        plugins["updater"] = upd

    c.write(base_conf, json.dumps(conf, ensure_ascii=False, indent=2) + "\n", dry_run)
    c.log("neutered updater (kept pubkey, disabled artifacts, dead endpoint)")


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
    # 2b) Sidebar wordmark: upstream renders a "Clash Verge" logo SVG next to
    #     the app icon in the nav header. It's a vector wordmark (paths, not
    #     text), so it stays "Clash Verge" regardless of productName. Swap the
    #     <LogoSvg> render for a brand-name text node and drop the now-unused
    #     import (web:build runs tsc --noEmit before vite build).
    layout = client_dir / "src/pages/_layout.tsx"
    c.regex_replace(
        layout,
        r"import LogoSvg from '@/assets/image/logo\.svg\?react'\n",
        "",
        dry_run, required=False,
    )
    c.replace_once(
        layout,
        "<LogoSvg fill={isDark ? 'white' : 'black'} />",
        (
            "<span\n"
            "                  style={{\n"
            "                    fontSize: '18px',\n"
            "                    fontWeight: 700,\n"
            "                    lineHeight: '27px',\n"
            "                    whiteSpace: 'nowrap',\n"
            "                    color: isDark ? 'white' : 'black',\n"
            "                  }}\n"
            "                >\n"
            f"                  {app_name}\n"
            "                </span>"
        ),
        dry_run, required=False,
    )
    c.replace_once(
        client_dir / "src-tauri/src/core/tray/mod.rs",
        '"Clash Verge {}\\n{}: {}\\n{}: {}\\n{}: {}"',
        f'"{app_name} {{}}\\n{{}}: {{}}\\n{{}}: {{}}\\n{{}}: {{}}"',
        dry_run, required=False,
    )

    # 3) Disable upstream auto-update WITHOUT breaking the updater plugin.
    #
    #    CRITICAL: the tauri-plugin-updater initialises from plugins.updater and
    #    REQUIRES a `pubkey` field. On macOS/Windows the plugin init deserialises
    #    the config eagerly, so dropping `pubkey` (as an earlier version did) makes
    #    generate_context! / build() fail with:
    #        PluginInitialization("updater", "... missing field `pubkey`")
    #    and the app exits(1) immediately on launch ("installs but won't start").
    #    Linux's updater path doesn't hit this, which is why it looked fine there.
    #
    #    So we KEEP a valid pubkey and instead neuter updates the safe way:
    #      - createUpdaterArtifacts=false: no signed update artifacts are produced;
    #      - endpoints -> a dead/brand URL: the app can never fetch an update
    #        manifest (and we never publish one / never hold the private key), so
    #        it can't be silently replaced by the upstream build.
    #    This is done with a JSON rewrite so the block stays well-formed and the
    #    required `pubkey` is guaranteed present.
    base_conf = client_dir / "src-tauri/tauri.conf.json"
    _neuter_updater(base_conf, brand, dry_run)

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

        # 4b) Prominent drainage: a full-width banner card at the TOP of the home
        #     page. The settings-header gift icon above is easy to miss, so this
        #     puts an obvious membership call-to-action on the first screen the
        #     user sees. Reuses home.tsx's existing openWebUrl + useLockFn +
        #     Box/Grid imports; only adds a handler and a Grid item. Anchors are
        #     from upstream v2.5.x home.tsx and matched exactly.
        home = client_dir / "src/pages/home.tsx"
        sub = rec.get("subtitle", "")
        btn_text = rec.get("buttonText", "\u7acb\u5373\u5f00\u901a")
        c.insert_after(
            home,
            "  const toGithubDoc = useLockFn(() => {\n"
            "    return openWebUrl('https://clash-verge-rev.github.io/index.html')\n"
            "  })\n",
            (
                "\n  const toBrandPurchase = useLockFn(() => {\n"
                f"    return openWebUrl('{rec['purchaseUrl']}')\n"
                "  })\n"
            ),
            dry_run, required=False,
        )
        banner = (
            "\n        <Grid size={12}>\n"
            "          <Box\n"
            "            onClick={toBrandPurchase}\n"
            "            sx={{\n"
            "              cursor: 'pointer',\n"
            "              borderRadius: 2,\n"
            "              px: 2.5,\n"
            "              py: 1.75,\n"
            "              display: 'flex',\n"
            "              alignItems: 'center',\n"
            "              justifyContent: 'space-between',\n"
            "              gap: 2,\n"
            "              color: '#fff',\n"
            "              background:\n"
            "                'linear-gradient(135deg, #23b79c 0%, #1b8f7a 100%)',\n"
            "              boxShadow: '0 6px 18px rgba(35,183,156,0.35)',\n"
            "              transition: 'transform .2s ease, box-shadow .2s ease',\n"
            "              '&:hover': {\n"
            "                transform: 'translateY(-2px)',\n"
            "                boxShadow: '0 10px 24px rgba(35,183,156,0.5)',\n"
            "              },\n"
            "            }}\n"
            "          >\n"
            "            <Box sx={{ minWidth: 0 }}>\n"
            f"              <Box sx={{{{ fontSize: 16, fontWeight: 700 }}}}>{title}</Box>\n"
            f"              <Box sx={{{{ fontSize: 12.5, opacity: 0.92, mt: 0.25 }}}}>{sub}</Box>\n"
            "            </Box>\n"
            "            <Box\n"
            "              sx={{\n"
            "                flexShrink: 0,\n"
            "                px: 2,\n"
            "                py: 0.75,\n"
            "                borderRadius: 1.5,\n"
            "                fontSize: 13.5,\n"
            "                fontWeight: 700,\n"
            "                whiteSpace: 'nowrap',\n"
            "                color: '#1b8f7a',\n"
            "                background: '#fff',\n"
            "              }}\n"
            "            >\n"
            f"              {btn_text}\n"
            "            </Box>\n"
            "          </Box>\n"
            "        </Grid>\n"
        )
        c.insert_after(
            home,
            "<Grid container spacing={1.5} columns={{ xs: 6, sm: 6, md: 12 }}>",
            banner,
            dry_run, required=False,
        )

    # 5) Pin the mihomo (verge-mihomo) core to a version the bundled
    #    tauri-plugin-mihomo can deserialize. See MIHOMO_PINNED_VERSION above:
    #    upstream prebuild.mjs downloads the LATEST stable, but a newer core
    #    (e.g. v1.19.27) changes /configs & /proxies fields the strict plugin
    #    can't parse -> getBaseConfig/getProxies fail -> empty proxy groups &
    #    "内核通信错误". Force META_VERSION to the pinned stable in
    #    getLatestReleaseVersion() (returns early, bypassing the latest fetch).
    c.regex_replace(
        client_dir / "scripts/prebuild.mjs",
        r"async function getLatestReleaseVersion\(\) \{\n  if \(!FORCE\) \{",
        (
            "async function getLatestReleaseVersion() {\n"
            f"  META_VERSION = '{MIHOMO_PINNED_VERSION}'\n"
            "  log_info(`Pinned release version: ${META_VERSION}`)\n"
            "  await setCachedVersion('META_VERSION', META_VERSION)\n"
            "  return\n"
            "  // eslint-disable-next-line no-unreachable\n"
            "  if (!FORCE) {"
        ),
        dry_run, required=False,
    )

    # 6) Rebrand the Rust-side notification/tray/service strings shipped in the
    #    separate `clash-verge-i18n` crate (crates/clash-verge-i18n/locales/*.yml).
    #    These are NOT under src/locales (the front-end i18n this adapter already
    #    rebrands elsewhere) -- they're a standalone crate consumed by the Rust
    #    backend for system notifications ("Clash Verge is running in the
    #    background", "Clash Verge is about to exit", service install prompts,
    #    etc). Missing this made the app show the upstream name on every
    #    minimize/quit/service-prompt notification across all 13 languages,
    #    even though the window title/tray/sidebar were already rebranded.
    i18n_crate = client_dir / "crates/clash-verge-i18n"
    i18n_dir = i18n_crate / "locales"
    if i18n_dir.is_dir():
        yml_files = sorted(i18n_dir.glob("*.yml"))
        for locale_file in yml_files:
            c.regex_replace(
                locale_file,
                r"Clash Verge",
                app_name,
                dry_run, count=0, required=False,
            )
        c.log("rebranded clash-verge-i18n locales (notifications/tray/service)")

        # CRITICAL: rust-i18n compiles the locale YAMLs into the binary via the
        # i18n!() macro at COMPILE time. But Cargo's fingerprint only tracks .rs
        # sources + Cargo.toml, NOT the crate's *.yml resource files. So on CI
        # with a warm Rust cache (Swatinem/rust-cache reuses target/), editing
        # only the YAMLs does NOT trigger a recompile of this crate's macro
        # expansion -> the built binary keeps the OLD (upstream "Clash Verge")
        # translations even though the YAML on disk is rebranded. This is a
        # known rust-i18n issue (longbridge/rust-i18n#46): "only cargo clean
        # works". Two belt-and-suspenders fixes so a cache hit can't ship stale
        # strings:
        #
        #   a) build.rs with per-file `rerun-if-changed` (dir-level watching is
        #      unreliable: Cargo only checks dir mtime, not file contents).
        #   b) inject a brand-specific marker comment into lib.rs so the .rs
        #      fingerprint itself differs per brand, forcing rustc to re-expand
        #      the i18n!() macro (Cargo always tracks .rs changes).
        rerun_lines = "\n".join(
            f'    println!("cargo:rerun-if-changed=locales/{p.name}");'
            for p in yml_files
        )
        build_rs = (
            "// Auto-generated by ClientFactory adapter: force Cargo to re-run\n"
            "// (and re-expand rust-i18n's i18n!() macro) whenever a locale YAML\n"
            "// changes, so brand rebrand of notification/tray strings can't be\n"
            "// silently skipped by a warm build cache. See rust-i18n#46.\n"
            "fn main() {\n"
            "    println!(\"cargo:rerun-if-changed=locales\");\n"
            f"{rerun_lines}\n"
            "}\n"
        )
        c.ensure_file(i18n_crate / "build.rs", build_rs, dry_run)

        # Make Cargo.toml explicitly reference build.rs (edition 2024 auto-detects
        # it, but being explicit is harmless and unambiguous).
        cargo_toml = i18n_crate / "Cargo.toml"
        c.regex_replace(
            cargo_toml,
            r'(edition = "2024")\n',
            r'\1\nbuild = "build.rs"\n',
            dry_run, count=1, required=False,
        )

        # Brand-specific fingerprint marker on lib.rs (guarantees .rs change per
        # brand -> guaranteed macro re-expansion regardless of cache state).
        lib_rs = i18n_crate / "src/lib.rs"
        c.regex_replace(
            lib_rs,
            r"use rust_i18n::i18n;\n",
            f"// brand: {app_name}\nuse rust_i18n::i18n;\n",
            dry_run, count=1, required=False,
        )
        c.log("added build.rs rerun-if-changed + brand fingerprint (defeat i18n build-cache staleness)")
    else:
        c.log("skip (dir not found): crates/clash-verge-i18n/locales")

    c.log("clash-verge-rev adapter applied")
