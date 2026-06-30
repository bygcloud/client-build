"""ClashMetaForAndroid adapter (Android / Kotlin).

Applies:
1. Brand: launch_name_meta / application_name_meta strings -> app name; the
   default applicationId is set to the brand package id.
2. Optional recommendation entry: a category + clickable row at the top of the
   Help screen (HelpDesign) that opens a configured URL via openLink(...); adds
   the matching string resources.
"""
from __future__ import annotations

from pathlib import Path

import _common as c


def apply(client_dir: Path, cfg: dict, dry_run: bool = False) -> None:
    brand = cfg["brand"]
    rec = cfg.get("recommend", {})
    name = brand["appName"]
    title = rec.get("title", "")
    url = rec.get("purchaseUrl", "")

    # 1) App name (meta flavor is the default channel)
    design_strings = client_dir / "design/src/main/res/values/strings.xml"
    c.regex_replace(
        design_strings,
        r'<string name="launch_name_meta">[^<]*</string>',
        f'<string name="launch_name_meta">{name}</string>',
        dry_run,
    )
    c.regex_replace(
        design_strings,
        r'<string name="application_name_meta">[^<]*</string>',
        f'<string name="application_name_meta">{name}</string>',
        dry_run,
    )

    # add recommendation string resources (title + URL)
    rec_strings = (
        f'\n    <string name="recommend_category">{title}</string>'
        f'\n    <string name="recommend_title">{rec.get("subtitle", title)}</string>'
        f'\n    <string name="recommend_url" translatable="false">{url}</string>'
    )
    c.insert_after(
        design_strings,
        '<resources xmlns:tools="http://schemas.android.com/tools" tools:ignore="PluralsCandidate">',
        rec_strings,
        dry_run,
    )

    # 2) default applicationId
    root_gradle = client_dir / "build.gradle.kts"
    c.regex_replace(
        root_gradle,
        r'\?: "com\.github\.metacubex\.clash"',
        f'?: "{brand["packageId"]}"',
        dry_run, required=False,
    )

    # 3) inject the recommendation entry into HelpDesign
    if rec.get("enabled", False) and url:
        help_design = client_dir / "design/src/main/java/com/github/kr328/clash/design/HelpDesign.kt"
        snippet = (
            "\n            category(R.string.recommend_category)\n\n"
            "            clickable(\n"
            "                title = R.string.recommend_title,\n"
            "                summary = R.string.recommend_url\n"
            "            ) {\n"
            "                clicked {\n"
            "                    openLink(Uri.parse(context.getString(R.string.recommend_url)))\n"
            "                }\n"
            "            }\n"
        )
        c.insert_after(
            help_design,
            "tips(R.string.tips_help)\n",
            snippet,
            dry_run,
        )

    c.log("ClashMetaForAndroid adapter applied")
