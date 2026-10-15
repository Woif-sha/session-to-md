#!/usr/bin/env python3
"""Build the versioned skill archive and Chinese notes from CHANGELOG.md."""

import argparse
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_FILES = (
    "CHANGELOG.md", "LICENSE", "README.md", "SKILL.md", "VERSION",
    "agents/openai.yaml", "references/upstream.md", "scripts/session_to_md.py",
)
REPOSITORY = "https://github.com/Woif-sha/session-to-md"


def build(version: str, destination: Path):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Use an X.Y.Z release version")
    if version != (ROOT / "VERSION").read_text(encoding="utf-8").strip():
        raise ValueError("Requested version does not match VERSION")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(
        rf"^## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}\n(.*?)(?=^## |\Z)",
        changelog, re.M | re.S,
    )
    if not section:
        raise ValueError("Version is missing from CHANGELOG.md")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"session-to-md-v{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as package:
        for relative in PACKAGE_FILES:
            entry = zipfile.ZipInfo(f"session-to-md/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            package.writestr(entry, (ROOT / relative).read_bytes())
    notes = destination / f"release-v{version}.md"
    notes.write_text(
        f"# [**v{version}**]({REPOSITORY}/releases/tag/v{version})\n\n"
        f"## 更新内容\n\n{section.group(1).strip()}\n\n"
        "## 安装与更新\n\n"
        f"下载本页的 `session-to-md-v{version}.zip`，将其中的 `session-to-md` 文件夹解压到 `~/.agents/skills/`。\n\n"
        "也可以使用 Git 安装：\n\n```powershell\n"
        f'git clone {REPOSITORY}.git "$HOME/.agents/skills/session-to-md"\n```\n\n'
        "安装后在目标 Codex 会话中手动调用 `$session-to-md`，无需附加参数。已有 Git 安装可执行 `git pull --ff-only` 更新。\n\n"
        "## 发布校验\n\n"
        "- 发布工作流在构建前运行完整行为测试。\n"
        "- 安装包按固定文件清单构建，不包含真实会话导出、本地 `.openskills.json` 或 `.git` 数据。\n"
        "- 版本号与 VERSION 一致；已有版本不覆盖。\n\n"
        "## 完整记录\n\n"
        f"[版本记录]({REPOSITORY}/blob/v{version}/CHANGELOG.md) · "
        f"[使用说明]({REPOSITORY}/blob/v{version}/README.md)\n",
        encoding="utf-8", newline="\n",
    )
    return archive, notes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for path in build(args.version, args.output_dir):
        print(path.resolve())
