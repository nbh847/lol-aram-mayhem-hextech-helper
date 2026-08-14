"""维护者入口：全量更新数据并推送到项目远端。"""
from datetime import datetime
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = (
    Path("data/hero_augments.csv"),
    Path("data/champions.json"),
    Path("data/pinyin_map.json"),
)
EXPECTED_REMOTES = {
    "git@github.com:nbh847/lol-aram-mayhem-hextech-helper.git",
    "https://github.com/nbh847/lol-aram-mayhem-hextech-helper.git",
}


def find_git():
    """优先使用 PATH 中的 Git，否则查找 GitHub Desktop 自带的 Git。"""
    git = shutil.which("git")
    if git:
        return Path(git)

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None

    candidates = sorted(
        Path(local_app_data).glob(
            "GitHubDesktop/app-*/resources/app/git/cmd/git.exe"
        ),
        reverse=True,
    )
    return candidates[0] if candidates else None


def run_git(git_path, args, capture_output=False):
    command = [
        str(git_path),
        "--git-dir",
        str(PROJECT_ROOT / ".git"),
        "--work-tree",
        str(PROJECT_ROOT),
        *args,
    ]
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.STDOUT if capture_output else None,
        check=False,
    )


def git_output(git_path, args):
    result = run_git(git_path, args, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or f"Git 命令失败: {' '.join(args)}")
    return result.stdout.strip()


def validate_data_files():
    """确认更新后的三个数据文件可读且不是空文件。"""
    csv_path = PROJECT_ROOT / DATA_FILES[0]
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required_fields = {"英文名", "等级", "海克斯名称"}
        if not reader.fieldnames or not required_fields.issubset(reader.fieldnames):
            raise RuntimeError(f"CSV 表头不完整: {csv_path}")
        row_count = sum(1 for _ in reader)
    if row_count == 0:
        raise RuntimeError(f"CSV 没有数据行: {csv_path}")

    for relative_path in DATA_FILES[1:]:
        path = PROJECT_ROOT / relative_path
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict) or not value:
            raise RuntimeError(f"JSON 为空或格式错误: {path}")

    print(f"✅ 数据校验通过: {row_count} 条海克斯记录")


def ensure_git_preconditions(git_path):
    if not (PROJECT_ROOT / ".git").is_dir():
        raise RuntimeError("当前目录不是 Git 仓库")

    branch = git_output(git_path, ["branch", "--show-current"])
    if branch != "main":
        raise RuntimeError(f"当前分支是 {branch or ' detached HEAD'}，要求在 main 分支执行")

    remote = git_output(git_path, ["remote", "get-url", "origin"])
    if remote.rstrip("/") not in EXPECTED_REMOTES:
        raise RuntimeError(f"origin 不是目标仓库: {remote}")

    staged_check = run_git(git_path, ["diff", "--cached", "--quiet"])
    if staged_check.returncode != 0:
        raise RuntimeError("暂存区已有改动，请先提交或清空后再执行")


def main():
    git_path = find_git()
    if not git_path:
        print("❌ 未找到 Git。请安装 Git 或 GitHub Desktop。")
        return 1

    try:
        ensure_git_preconditions(git_path)

        sys.path.insert(0, str(PROJECT_ROOT))
        from scripts.hero_scraper import is_chrome_installed
        from scripts.updater import run_update

        if not is_chrome_installed():
            raise RuntimeError("未检测到 Google Chrome，全量爬取无法执行")

        messages = []

        def log(message):
            print(message)
            messages.append(str(message))

        print("=== 维护者数据更新开始（全量模式）===")
        success = run_update(mode="full", log_func=log)
        if not success:
            raise RuntimeError("全量更新失败，不提交远程数据")
        if any("爬取失败的英雄" in message for message in messages):
            raise RuntimeError("部分英雄爬取失败，不提交不完整数据")

        validate_data_files()

        relative_files = [str(path).replace("/", "\\") for path in DATA_FILES]
        add_result = run_git(git_path, ["add", "--", *relative_files])
        if add_result.returncode != 0:
            raise RuntimeError("Git 暂存数据文件失败")

        changed_files = git_output(git_path, ["diff", "--cached", "--name-only"])
        if not changed_files:
            print("✅ 数据没有变化，无需提交或推送")
            return 0

        commit_message = (
            f"data: refresh ARAM augment data "
            f"({datetime.now().strftime('%Y-%m-%d')})"
        )
        commit_result = run_git(git_path, ["commit", "-m", commit_message])
        if commit_result.returncode != 0:
            raise RuntimeError("提交数据失败")

        push_result = run_git(git_path, ["push", "origin", "main"])
        if push_result.returncode != 0:
            raise RuntimeError("推送数据失败，请检查远端变更和 Git 凭据")

        print("✅ 数据已提交并推送到 origin/main")
        return 0
    except KeyboardInterrupt:
        print("\n⏹ 用户中断，未执行提交推送")
        return 130
    except Exception as exc:
        print(f"❌ {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
