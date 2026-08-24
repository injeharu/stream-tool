"""変更内容をコミットしてGitHubへプッシュするだけの簡易ツール。

使い方: commit.bat をダブルクリック。
バージョンを上げてリリースする場合は release.bat を使ってください。
"""

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, check=True, quiet=False):
    # git addの改行コード警告は大量に出て読みにくいため、必要な場面では抑制する
    stderr = subprocess.DEVNULL if quiet else None
    result = subprocess.run(cmd, cwd=BASE_DIR, stderr=stderr)
    if check and result.returncode != 0:
        print(f"\nコマンドが失敗しました(終了コード {result.returncode})。中断します。")
        sys.exit(1)
    return result.returncode


def main():
    print("=== 変更をコミットしてプッシュ ===\n")

    # 変更点を一覧表示(何が保存されるか目で確認できるように)
    print("【変更されたファイル】")
    status = subprocess.run(
        ["git", "status", "--short"], cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8"
    )
    if not status.stdout.strip():
        print("  変更はありません。終了します。")
        return
    print(status.stdout)

    message = input("コミットメッセージ(何を変えたか)を入力してください: ").strip()
    if not message:
        print("メッセージが空のため中断しました。")
        return

    run(["git", "add", "-A"], quiet=True)

    answer = input("\nこの内容でコミット・プッシュしますか? (y/n): ").strip().lower()
    if answer != "y":
        print("中断しました(変更はステージされたまま残ります)。")
        return

    full_message = f"{message}\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    if run(["git", "commit", "-m", full_message], check=False) != 0:
        print("コミットするものがありませんでした。")
        return

    run(["git", "push"])
    print("\n✅ プッシュまで完了しました。")


if __name__ == "__main__":
    main()
