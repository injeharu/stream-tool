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

        # 更新で本体が終了する前に、画面に残っている通知を片付けておく
        try:
            import notifier
            notifier.clear_all()
        except Exception:
            pass

        # インストーラーは実行中の本体を自動終了させてから上書きする(setup.iss側の仕組み)。
        # このプロセス自身も終了させられるため、独立したbatに「インストール→本体再起動」を任せる。
        # (cmd /c に引用符入りの1行を渡す方式は引用符の解釈で壊れるため、batファイル経由にする)
        restart_target = sys.executable
        bat_path = os.path.join(tempfile.gettempdir(), "tokuten_update.bat")
        # インストーラーが実行できなかったとき(SmartScreen等のブロック)に印を残すファイル
        fail_marker = os.path.join(tempfile.gettempdir(), "tokuten_update_failed.txt")
        try:
            os.remove(fail_marker)
        except OSError:
            pass
        # 文字化けしたパスで実行してしまわないよう、変換できない文字があれば素直に失敗させる
        with open(bat_path, "w", encoding="cp932", errors="strict") as f:
            f.write("@echo off\r\n")
            # VERYSILENT: インストーラーの進捗ウィンドウも出さない(画面は更新中表示のまま)
            f.write(f'"{dest}" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES\r\n')
            # 実行がブロックされた・失敗した場合は印を残す(本体側の見張りが検知する)
            f.write(f'if errorlevel 1 echo blocked > "{fail_marker}"\r\n')
            # ファイル差し替え直後の起動失敗を避けるため少し待ってから再起動する
            # (timeoutコマンドはコンソールが無いと動かないため、ping方式で約2秒待つ)
            f.write("ping -n 3 127.0.0.1 >nul\r\n")
            # 本体を新しいプロセスとして起動する。/B を付けないと
            # 非表示batから起動された本体が一瞬コンソールを出すことがある
            f.write(f'start "" /B "{restart_target}"\r\n')
            f.write('del "%~f0"\r\n')

        # CREATE_NO_WINDOW: batもその中のコマンドも一切ウィンドウを出さない
        # (DETACHEDだとbat内のpingが一瞬黒い窓を出してしまう)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(["cmd", "/c", bat_path], creationflags=flags, close_fds=True)

        # 見張り: インストールが成功すればこのプロセスは終了させられる。
        # 一定時間たっても生きている=インストーラーが動けなかった(ブロック等)ということなので、
        # 「インストール中」のまま固まらないようエラーに切り替えて、再試行できる状態に戻す。
        threading.Thread(target=_watch_blocked_install, args=(fail_marker,), daemon=True).start()
    except Exception as e:
        state.set_update_progress("error", f"更新に失敗しました: {e}")


def _watch_blocked_install(fail_marker, wait_seconds=60):
    time.sleep(wait_seconds)
    # ここに到達した=まだ生きている=更新は行われていない
    if os.path.exists(fail_marker):
        try:
            os.remove(fail_marker)
        except OSError:
            pass
        message = ("インストーラーの実行がWindowsにブロックされたようです(署名のない個人開発アプリのため)。"
                   "手動でインストールすれば更新できます。データは消えません")
    else:
        message = ("更新が完了しませんでした(セキュリティソフトにブロックされた可能性があります)。"
                   "手動でインストールすれば更新できます。データは消えません")
    state.set_update_progress("error", message)


def smart_app_control_on():
    """スマートアプリコントロール(SAC)が有効(強制モード)かどうか。

    SACが有効なPCでは、署名のないこのアプリのインストーラーは実行できず、
    SmartScreenと違って「詳細情報→実行」の抜け道も無い。
    無駄な更新試行をさせないため、事前に検出して正直に案内する。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")
        return value == 1  # 0=オフ, 1=有効, 2=評価モード(ブロックはしない)
    except OSError:
        return False


def start_one_click_update():
    """ワンクリック更新を開始する。exe版でのみ使用可能。"""
    if not config.IS_FROZEN:
        return False
    if state.get_update_progress()["phase"] in ("downloading", "installing"):
        return True  # すでに進行中

    if smart_app_control_on():
        state.set_update_progress(
            "error",
            "お使いのPCは「スマートアプリコントロール」が有効なため、署名のない"
            "このアプリは自動でも手動でも更新できません。今のバージョンのまま使い続けるか、"
            "READMEの「更新がWindowsにブロックされたとき」をご覧ください",
        )
        return True

    threading.Thread(target=_run_update, daemon=True).start()
    return True
