"""最新版の確認とワンクリック更新(GitHub Releases想定)。UPDATE_CHECK_URLが未設定なら何もしない。"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import config
import db
import state

# ダウンロードの上限(異常なファイルを掴まされないための保険)
MAX_INSTALLER_BYTES = 200 * 1024 * 1024


def _parse_version(v):
    v = v.lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _is_newer(remote_version, current_version):
    return _parse_version(remote_version) > _parse_version(current_version)


def check_once(force=False):
    """1回だけ最新版を確認する。失敗しても例外を外に出さない(起動を邪魔しないため)。
    force=True は設定画面の「今すぐ確認」ボタン用(自動確認OFFでも実行する)。
    成功時True、失敗時Falseを返す。"""
    if not config.UPDATE_CHECK_URL:
        return False
    if not force and not db.is_update_check_enabled():
        return False

    try:
        req = urllib.request.Request(
            config.UPDATE_CHECK_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "stream-shipping-tool"},
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            data = json.loads(res.read().decode("utf-8"))

        remote_version = data.get("tag_name", "")
        release_url = data.get("html_url", "")

        if remote_version and _is_newer(remote_version, config.APP_VERSION):
            state.set_available_update({"version": remote_version.lstrip("vV"), "url": release_url})
        else:
            state.set_available_update(None)
        return True
    except Exception as e:
        print(f"[更新確認エラー] {e}")
        return False


def run_forever():
    """バックグラウンドスレッドで定期的に確認し続ける。"""
    while True:
        check_once()
        time.sleep(config.UPDATE_CHECK_INTERVAL_SECONDS)


def start_background_check():
    if not config.UPDATE_CHECK_URL:
        return
    thread = threading.Thread(target=run_forever, daemon=True)
    thread.start()


# ---------- ワンクリック更新(exe版のみ) ----------

def _fetch_latest_installer():
    """最新リリースからインストーラーの情報(バージョン・URL・サイズ)を取得する。"""
    req = urllib.request.Request(
        config.UPDATE_CHECK_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "stream-shipping-tool"},
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        data = json.loads(res.read().decode("utf-8"))

    version = data.get("tag_name", "").lstrip("vV")
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("TokutenDaicho-Setup-") and name.endswith(".exe"):
            return version, asset["browser_download_url"], asset.get("size", 0)
    return version, None, 0


def _download(url, dest_path, expected_size):
    req = urllib.request.Request(url, headers={"User-Agent": "stream-shipping-tool"})
    downloaded = 0
    with urllib.request.urlopen(req, timeout=30) as res, open(dest_path, "wb") as f:
        while True:
            chunk = res.read(1024 * 256)
            if not chunk:
                break
            downloaded += len(chunk)
            if downloaded > MAX_INSTALLER_BYTES:
                raise ValueError("ダウンロードサイズが上限を超えました")
            f.write(chunk)
    if expected_size and downloaded != expected_size:
        raise ValueError(f"サイズ不一致(期待{expected_size} / 実際{downloaded})")
    return downloaded


def _run_update():
    """ダウンロード→サイレントインストール→再起動。別スレッドで実行される。"""
    try:
        state.set_update_progress("downloading", "新しいバージョンを取得しています")
        version, url, size = _fetch_latest_installer()
        if not url:
            state.set_update_progress("error", "リリースにインストーラーが見つかりませんでした")
            return
        if not url.startswith("https://"):
            state.set_update_progress("error", "ダウンロード先が不正なため中止しました")
            return

        dest = os.path.join(tempfile.gettempdir(), f"TokutenDaicho-Setup-v{version}.exe")
        _download(url, dest, size)

        state.set_update_progress("installing", f"v{version} をインストールしています")

        # インストーラーは実行中の本体を自動終了させてから上書きする(setup.iss側の仕組み)。
        # このプロセス自身も終了させられるため、独立したbatに「インストール→本体再起動」を任せる。
        # (cmd /c に引用符入りの1行を渡す方式は引用符の解釈で壊れるため、batファイル経由にする)
        restart_target = sys.executable
        bat_path = os.path.join(tempfile.gettempdir(), "tokuten_update.bat")
        # 文字化けしたパスで実行してしまわないよう、変換できない文字があれば素直に失敗させる
        with open(bat_path, "w", encoding="cp932", errors="strict") as f:
            f.write("@echo off\r\n")
            f.write(f'"{dest}" /SILENT /NORESTART\r\n')
            # ファイル差し替え直後の起動失敗を避けるため少し待ってから再起動する
            f.write("timeout /t 2 /nobreak >nul\r\n")
            f.write(f'start "" "{restart_target}"\r\n')
            f.write('del "%~f0"\r\n')

        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(["cmd", "/c", bat_path], creationflags=flags, close_fds=True)
    except Exception as e:
        state.set_update_progress("error", f"更新に失敗しました: {e}")


def start_one_click_update():
    """ワンクリック更新を開始する。exe版でのみ使用可能。"""
    if not config.IS_FROZEN:
        return False
    if state.get_update_progress()["phase"] in ("downloading", "installing"):
        return True  # すでに進行中
    threading.Thread(target=_run_update, daemon=True).start()
    return True
