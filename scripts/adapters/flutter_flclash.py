"""FlClash 适配器（Flutter，Clash/Mihomo 内核，主用于安卓品牌包）

植入内容：
1. 品牌：lib/common/constant.dart 的 appName（中文显示名）
2. 安卓桌面名：AndroidManifest.xml 的 android:label
3. 安卓包名：android/app/build.gradle.kts 的 applicationId
4. 推荐机场：「关于」页 More 区块顶部插入「开通会员/推荐机场」入口，
   复用 globalState.openUrl(recommendUrl) 打开购买页
5. 去 Firebase/Crashlytics：FlClash 安卓构建强依赖 google-services.json，
   缺了会编译失败。品牌包不需要其崩溃分析，且不应把用户数据上报到
   非我方的 Firebase，故彻底移除（gradle 插件 + 依赖 + Kotlin 调用置 no-op）。
6. 版本检查源：repository 从上游 chen08209/FlClash 改为品牌自有仓库，
   防止提示不存在的「新版本」和暴露底层 FlClash 品牌。
7. 关闭自动更新：autoCheckUpdate 默认值从 true 改 false，
   品牌包版本由我方控制，不随上游自动提示。
8. 品牌化免责声明：disclaimerDesc 中「本软件」替换为品牌名。

图标：用 sips + cwebp 把 brand.iconPath 生成各密度 mipmap（legacy + adaptive
前景 bitmap），并把 adaptive XML 的 foreground 指到 @mipmap/ic_launcher_foreground、
背景色改成 brand.primaryColor。本适配器只处理文本/配置，图标由 build/打包脚本调用。

注意：CI 自包含化（去 .gitmodules，内嵌 core/Clash.Meta + flutter_distributor
+ tray_manager）由建仓脚本处理，不在本适配器内。
"""
from __future__ import annotations

from pathlib import Path

import _common as c


def apply(client_dir: Path, cfg: dict, dry_run: bool = False) -> None:
    brand = cfg["brand"]
    rec = cfg["recommend"]
    app_name = brand["appName"]  # 中文显示名

    # 1) 品牌常量 appName + 注入推荐常量
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
        f"\nconst recommendUrl = '{rec['purchaseUrl']}';\n"
        f"const recommendTitle = '{rec.get('title', '推荐机场')}';",
        dry_run, required=False,
    )

    # 2) 安卓桌面显示名（launcher 图标下方文字）
    manifest = client_dir / "android/app/src/main/AndroidManifest.xml"
    c.regex_replace(
        manifest,
        r'android:label="[^"]*"',
        f'android:label="{app_name}"',
        dry_run, count=0, required=False,  # count=0 = 全部替换
    )

    # 3) 安卓 applicationId（只改主包名，.dev 后缀逻辑保留）
    gradle = client_dir / "android/app/build.gradle.kts"
    c.regex_replace(
        gradle,
        r'applicationId = "com\.follow\.clash"',
        f'applicationId = "{brand["packageId"]}"',
        dry_run, required=False,
    )

    # 4) 去 Firebase/Crashlytics（避免 google-services.json 硬依赖 + 隐私上报）
    _strip_firebase(client_dir, dry_run)

    # 6) 版本检查源改为品牌自有仓库
    github_repo = brand.get("githubRepo", brand.get("updaterRepo", ""))
    if github_repo:
        c.regex_replace(
            constant,
            r"const repository = '[^']*';",
            f"const repository = '{github_repo}';",
            dry_run, required=False,
        )

    # 7) 关闭自动更新默认值
    config_dart = client_dir / "lib/models/config.dart"
    c.regex_replace(
        config_dart,
        r"@Default\(true\) bool autoCheckUpdate",
        "@Default(false) bool autoCheckUpdate",
        dry_run, required=False,
    )

    # 8) 品牌化免责声明文案
    _brand_disclaimer(client_dir, app_name, dry_run)

    # 5) 关于页 More 区块注入推荐入口
    if rec.get("enabled", True):
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
        # 插在 More 区块 items 列表开头（用 More 标题做唯一锚点，避免命中贡献者区块）
        c.insert_after(
            about,
            "title: appLocalizations.more,\n      items: [",
            snippet,
            dry_run,
        )

    c.log("FlClash 植入完成")


def _strip_firebase(client_dir: Path, dry_run: bool) -> None:
    """移除 Firebase/Crashlytics：gradle 插件 + 依赖 + Kotlin 调用 no-op。"""
    # app 模块插件
    app_gradle = client_dir / "android/app/build.gradle.kts"
    c.regex_replace(
        app_gradle,
        r'\n\s*id\("com\.google\.gms\.google-services"\)'
        r'\n\s*id\("com\.google\.firebase\.crashlytics"\)',
        "",
        dry_run, required=False,
    )
    # app 模块依赖
    c.regex_replace(
        app_gradle,
        r'\n\s*implementation\(platform\(libs\.firebase\.bom\)\)'
        r'\n\s*implementation\(libs\.firebase\.crashlytics\.ndk\)'
        r'\n\s*implementation\(libs\.firebase\.analytics\)',
        "",
        dry_run, required=False,
    )
    # common 模块依赖
    common_gradle = client_dir / "android/common/build.gradle.kts"
    c.regex_replace(
        common_gradle,
        r'\n\s*implementation\(platform\(libs\.firebase\.bom\)\)'
        r'\n\s*implementation\(libs\.firebase\.crashlytics\.ndk\)'
        r'\n\s*implementation\(libs\.firebase\.analytics\)',
        "",
        dry_run, required=False,
    )
    # settings 插件声明
    settings = client_dir / "android/settings.gradle.kts"
    c.regex_replace(
        settings,
        r'\n\s*id\("com\.google\.gms\.google-services"\)[^\n]*'
        r'\n\s*id\("com\.google\.firebase\.crashlytics"\)[^\n]*',
        "",
        dry_run, required=False,
    )
    # Kotlin：setCrashlytics 置 no-op + 去 import
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
        '        // 品牌定制：移除 Firebase/Crashlytics，置为 no-op。\n'
        '    }',
        dry_run, required=False,
    )
    c.log("已移除 Firebase/Crashlytics")


def _brand_disclaimer(client_dir: Path, app_name: str, dry_run: bool) -> None:
    """将各语言免责声明中的通用表述替换为品牌名。"""
    l10n_dir = client_dir / "lib/l10n/intl"
    if not l10n_dir.exists():
        c.log("跳过（l10n 目录不存在）: 免责声明品牌化")
        return
    replacements = [
        ("messages_zh_CN.dart", "本软件", app_name),
        ("messages_ja.dart", "本ソフトウェア", app_name),
    ]
    for filename, old_text, new_text in replacements:
        f = l10n_dir / filename
        c.regex_replace(
            f,
            old_text,
            new_text,
            dry_run, count=0, required=False,
        )
    c.log(f"已品牌化免责声明 → {app_name}")
