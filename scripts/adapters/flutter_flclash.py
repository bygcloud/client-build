"""FlClash adapter (Flutter, Clash/Mihomo core; mainly for Android builds).

Applies:
1. Brand: appName in lib/common/constant.dart (display name).
2. Android launcher label: android:label in AndroidManifest.xml.
3. Android package: applicationId in android/app/build.gradle.kts.
4. Optional recommendation entry at the top of the About page's More section,
   opening a configured URL via globalState.openUrl(recommendUrl).
5. Strip Firebase/Crashlytics: the upstream Android build hard-depends on
   google-services.json and fails without it. White-label builds don't need its
   crash analytics and must not report user data to a third-party Firebase, so
   it is removed (gradle plugin + deps + Kotlin call made a no-op).

Icons are generated separately into per-density mipmaps (legacy + adaptive
foreground bitmap); this adapter only edits text/config.

Note: making the checkout self-contained (dropping .gitmodules, embedding the
core + plugin submodules) is handled by the prepare step, not here.
"""
from __future__ import annotations

from pathlib import Path

import _common as c


def apply(client_dir: Path, cfg: dict, dry_run: bool = False) -> None:
    brand = cfg["brand"]
    rec = cfg.get("recommend", {})
    app_name = brand["appName"]  # display name

    # 1) brand constant appName + inject recommendation constants
    constant = client_dir / "lib/common/constant.dart"
    c.regex_replace(
        constant,
        r"const appName = '[^']*';",
        f"const appName = '{app_name}';",
        dry_run,
    )
    c.insert_after(
        constant,
        f"const appName = '{app_name}';",
        f"\nconst recommendUrl = '{rec.get('purchaseUrl', '')}';\n"
        f"const recommendTitle = '{rec.get('title', '')}';",
        dry_run, required=False,
    )

    # 2) Android launcher label (text under the launcher icon)
    manifest = client_dir / "android/app/src/main/AndroidManifest.xml"
    c.regex_replace(
        manifest,
        r'android:label="[^"]*"',
        f'android:label="{app_name}"',
        dry_run, count=0, required=False,  # count=0 = replace all
    )

    # 3) Android applicationId (main package only; keep the .dev suffix logic)
    gradle = client_dir / "android/app/build.gradle.kts"
    c.regex_replace(
        gradle,
        r'applicationId = "com\.follow\.clash"',
        f'applicationId = "{brand["packageId"]}"',
        dry_run, required=False,
    )

    # 4) Strip Firebase/Crashlytics (avoid google-services.json hard dep + telemetry)
    _strip_firebase(client_dir, dry_run)

    # 5) Inject the recommendation entry into the About page's More section
    if rec.get("enabled", False) and rec.get("purchaseUrl"):
        about = client_dir / "lib/views/about.dart"
        subtitle = rec.get("subtitle", "")
        sub_line = f"          subtitle: const Text('{subtitle}'),\n" if subtitle else ""
        snippet = (
            "\n        ListItem(\n"
            f"          title: const Text(recommendTitle),\n"
            f"{sub_line}"
            "          onTap: () {\n"
            "            globalState.openUrl(recommendUrl);\n"
            "          },\n"
            "          trailing: const Icon(Icons.card_giftcard),\n"
            "        ),"
        )
        # insert at the top of the More section's items (anchor on the More title
        # to stay unique and avoid matching the contributors section)
        c.insert_after(
            about,
            "title: appLocalizations.more,\n      items: [",
            snippet,
            dry_run,
        )

    c.log("FlClash adapter applied")


def _strip_firebase(client_dir: Path, dry_run: bool) -> None:
    """Remove Firebase/Crashlytics: gradle plugins + deps + make Kotlin call no-op."""
    # app module plugins
    app_gradle = client_dir / "android/app/build.gradle.kts"
    c.regex_replace(
        app_gradle,
        r'\n\s*id\("com\.google\.gms\.google-services"\)'
        r'\n\s*id\("com\.google\.firebase\.crashlytics"\)',
        "",
        dry_run, required=False,
    )
    # app module deps
    c.regex_replace(
        app_gradle,
        r'\n\s*implementation\(platform\(libs\.firebase\.bom\)\)'
        r'\n\s*implementation\(libs\.firebase\.crashlytics\.ndk\)'
        r'\n\s*implementation\(libs\.firebase\.analytics\)',
        "",
        dry_run, required=False,
    )
    # common module deps
    common_gradle = client_dir / "android/common/build.gradle.kts"
    c.regex_replace(
        common_gradle,
        r'\n\s*implementation\(platform\(libs\.firebase\.bom\)\)'
        r'\n\s*implementation\(libs\.firebase\.crashlytics\.ndk\)'
        r'\n\s*implementation\(libs\.firebase\.analytics\)',
        "",
        dry_run, required=False,
    )
    # settings plugin declarations
    settings = client_dir / "android/settings.gradle.kts"
    c.regex_replace(
        settings,
        r'\n\s*id\("com\.google\.gms\.google-services"\)[^\n]*'
        r'\n\s*id\("com\.google\.firebase\.crashlytics"\)[^\n]*',
        "",
        dry_run, required=False,
    )
    # Kotlin: make setCrashlytics a no-op + drop imports
    gs = client_dir / "android/common/src/main/java/com/follow/clash/common/GlobalState.kt"
    c.regex_replace(
        gs,
        r'import com\.google\.firebase\.FirebaseApp\nimport com\.google\.firebase\.crashlytics\.FirebaseCrashlytics\n',
        "",
        dry_run, required=False,
    )
    c.regex_replace(
        gs,
        r'fun setCrashlytics\(enable: Boolean\) \{[\s\S]*?\n    \}',
        'fun setCrashlytics(enable: Boolean) {\n'
        '        // Firebase/Crashlytics removed; no-op.\n'
        '    }',
        dry_run, required=False,
    )
    c.log("removed Firebase/Crashlytics")
