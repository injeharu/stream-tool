"""新バージョンのコミット・プッシュ・GitHub Release作成・配布用ZIP添付を一括で行うツール。

使い方: release.bat をダブルクリック(内部でこのスクリプトを呼ぶ)。
GitHubトークンは github_token.txt に保存して再利用できる(.gitignore済み・配布ZIPにも入らない)。
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.py")
TOKEN_PATH = os.path.join(BASE_DIR, "github_token.txt")

sys.path.insert(0, BASE_DIR)
import config  # GITHUB_REPO を共有(リポジトリ名の二重管理を避ける)

GITHUB_REPO = config.GITHUB_REPO

EXCLUDE_DIR_NAMES = {
    ".git", "__pycache__", ".venv", "venv", "backups",
    "dist", "build", "dist_installer", "installer",
}
# github_token.txt は絶対に配布ZIPへ入れない(トークン流出防止)
# 開発者用のリリースツールも利用者には不要なため同梱しない
EXCLUDE_FILE_NAMES = {
    "data.db",
    "data.db-wal",
    "data.db-shm",
    "github_token.txt",
    ".gitignore",
    "commit.bat",
    "release.bat",
    "build_installer.bat",
    "commit.py",
    "release.py",
}
# 視聴者データのバックアップ(.bak)等も同梱しない(プライバシー保護)
EXCLUDE_SUFFIXES = (".db", ".db.bak", ".bak")


def run(cmd, **kwargs):
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=BASE_DIR, **kwargs)
    if result.returncode != 0:
        print(f"コマンドが失敗しました(終了コード {result.returncode})。中断します。")
        sys.exit(1)
    return result


def get_current_version():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', content)
    return m.group(1) if m else "0.0.0"


def set_version(new_version):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r'(APP_VERSION\s*=\s*)"[^"]+"', rf'\1"{new_version}"', content)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def build_zip(version):
    zip_name = f"stream-tool-v{version}.zip"
    zip_path = os.path.join(BASE_DIR, zip_name)
    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES]
            for name in files:
                if name in EXCLUDE_FILE_NAMES or name.startswith("stream-tool-v"):
                    continue
                if name.endswith(EXCLUDE_SUFFIXES):
                    continue
                full_path = os.path.join(root, name)
                arcname = os.path.relpath(full_path, BASE_DIR)
                zf.write(full_path, arcname)

    print(f"配布用ZIPを作成しました: {zip_name}")
    return zip_path


def github_api_request(url, token, method="GET", data=None, content_type="application/json"):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "stream-tool-release-script",
    }
    body = None
    if data is not None:
        if content_type == "application/json":
            body = json.dumps(data).encode("utf-8")
        else:
            body = data
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"GitHub APIエラー: {e.code} {e.reason}")
        print(e.read().decode("utf-8", errors="replace"))
        sys.exit(1)


def create_release(token, tag, notes):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    data = {
        "tag_name": tag,
        "name": tag,
        "body": notes,
        "draft": False,
        "prerelease": False,
    }
    return github_api_request(url, token, method="POST", data=data)


def upload_asset(token, upload_url_template, file_path, content_type="application/zip"):
    name = os.path.basename(file_path)
    upload_url = upload_url_template.split("{")[0] + f"?name={name}"
    with open(file_path, "rb") as f:
        content = f.read()
    print(f"アセットをアップロード中: {name}")
    github_api_request(upload_url, token, method="POST", data=content, content_type=content_type)
    print("アップロード完了。")


def find_installer(version):
    """build_installer.bat で作られたインストーラーがあれば、そのパスを返す。"""
    path = os.path.join(BASE_DIR, "dist_installer", f"TokutenDaicho-Setup-v{version}.exe")
    return path if os.path.exists(path) else None


def _find_iscc():
    """Inno SetupのコンパイラISCC.exeを探す。"""
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Inno Setup 6", "ISCC.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def build_installer(version):
    """バージョン更新後のコードでインストーラーをビルドする。
    (先にビルドすると旧バージョン番号のexeができてしまうため、リリース処理の中で行う)"""
    iscc = _find_iscc()
    if iscc is None:
        print("Inno Setupが見つからないため、インストーラーのビルドをスキップします。")
        print("(winget install JRSoftware.InnoSetup で導入できます)")
        return None

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstallerが見つからないため、インストーラーのビルドをスキップします。")
        print("(pip install pyinstaller で導入できます)")
        return None

    print("\nインストーラーをビルドしています(1〜2分かかります)...")
    run([sys.executable, "-m", "PyInstaller", os.path.join("installer", "tokuten.spec"),
         "--distpath", "dist", "--workpath", "build", "--noconfirm"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run([iscc, f"/DAppVersion={version}", os.path.join("installer", "setup.iss")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    path = find_installer(version)
    if path:
        print(f"インストーラーを作成しました: {os.path.basename(path)}")
    return path


def load_or_ask_token():
    """github_token.txt があればそれを使う。無ければ入力(コピペ可)を求め、保存するか選べる。"""
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            print(f"\n保存済みのトークンを使用します({os.path.basename(TOKEN_PATH)})。")
            return token

    print("\nGitHubの個人アクセストークン(repo権限)を貼り付けてください。")
    print("取得方法: GitHub右上のアイコン → Settings → Developer settings → Personal access tokens")
    token = input("トークン(右クリックで貼り付け): ").strip()
    if not token:
        return ""

    save = input("次回のためにgithub_token.txtへ保存しますか? このファイルはGitや配布ZIPには入りません (y/n): ").strip().lower()
    if save == "y":
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(token)
        print(f"保存しました: {TOKEN_PATH}")
    return token


def main():
    print("=== 配信サポートツール リリース作成ツール ===\n")

    current = get_current_version()
    print(f"現在のバージョン: {current}")
    new_version = input("新しいバージョン番号を入力してください(例: 1.1.0): ").strip()
    if not re.match(r"^\d+\.\d+\.\d+$", new_version):
        print("バージョン番号は 1.2.3 のような形式で入力してください。")
        sys.exit(1)

    print("\nリリースノート(更新内容)を入力してください。空行で入力終了:")
    notes_lines = []
    while True:
        line = input()
        if line == "":
            break
        notes_lines.append(line)
    notes = "\n".join(notes_lines) or f"v{new_version}"

    set_version(new_version)
    print(f"\nconfig.py の APP_VERSION を {new_version} に更新しました。")

    tag = f"v{new_version}"

    # 改行コードの警告が大量に出て読みにくいため抑制する
    run(["git", "add", "-A"], stderr=subprocess.DEVNULL)
    commit_result = subprocess.run(
        ["git", "commit", "-m", f"Release {tag}\n\n{notes}"], cwd=BASE_DIR
    )
    if commit_result.returncode != 0:
        print("(変更がなかったため、コミットはスキップされました)")
    run(["git", "push"])

    zip_path = build_zip(new_version)

    # バージョン更新後のコードでインストーラーを自動ビルドする
    installer_path = build_installer(new_version)
    if installer_path is None:
        # ビルド環境が無い場合、事前に作られたものがあればそれを使う
        installer_path = find_installer(new_version)
    if installer_path:
        print(f"リリースに添付するインストーラー: {os.path.basename(installer_path)}")
    else:
        print("インストーラーなしで続行します(ソースZIPのみ添付)。")

    token = load_or_ask_token()
    if not token:
        print("トークンが入力されなかったため、GitHub Releaseの作成をスキップしました。")
        print(f"コミット・プッシュ・ZIP作成({os.path.basename(zip_path)})は完了しています。")
        return

    print(f"\nGitHub Release {tag} を作成しています...")
    release = create_release(token, tag, notes)
    upload_asset(token, release["upload_url"], zip_path, content_type="application/zip")
    if installer_path:
        upload_asset(token, release["upload_url"], installer_path, content_type="application/octet-stream")

    print(f"\n✅ 完了しました: {release.get('html_url')}")
    print("これで旧バージョンを使っている人の画面左下に更新ボタンが表示されます。")


if __name__ == "__main__":
    main()
