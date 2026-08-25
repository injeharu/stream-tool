"""管理画面ウィンドウの起動と前面表示。

トレイの「管理画面を開く」を何度押しても多重にタブが増えないよう、
- 既に管理画面のウィンドウがあれば前面に持ってくるだけ
- 無ければ「アプリ風の専用ウィンドウ」(Edge/Chromeのアプリモード)で開く
という動きにする。Edge/Chromeが見つからない環境では従来どおり既定ブラウザで開く。
"""

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import config

ADMIN_URL = f"http://{config.FLASK_HOST}:{config.FLASK_PORT}"

# 全ページの<title>に含まれる目印(これでウィンドウを探す)
TITLE_MARK = "配信サポートツール"
# サーバー停止中に開いてしまった窓はエラーページになりタイトルがこれになる(これも自分の窓として扱う)
ERROR_TITLE = "127.0.0.1"


def _find_window():
    """管理画面を表示しているウィンドウを探す。"""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if TITLE_MARK in buf.value or buf.value.strip() == ERROR_TITLE:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def _bring_to_front(hwnd):
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):  # 最小化されていたら元に戻す
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def _find_browser():
    """アプリモードに対応したブラウザ(Edge/Chrome)を探す。"""
    candidates = [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ]
    for raw in candidates:
        path = os.path.expandvars(raw)
        if os.path.exists(path):
            return path
    return None


def _wait_for_server(timeout=10):
    """サーバーが応答を始めるまで少し待つ(起動直後にエラー画面を開かないため)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((config.FLASK_HOST, config.FLASK_PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


_open_lock = threading.Lock()


def _open_or_focus_blocking(url):
    # 同時に何度も押されても窓が複数開かないよう、処理は1つずつ行う
    with _open_lock:
        _wait_for_server()
        hwnd = _find_window()
        if hwnd:
            _bring_to_front(hwnd)
            return

        browser = _find_browser()
        if browser:
            # 専用プロファイルで独立したアプリ風ウィンドウとして起動する
            profile = os.path.join(config.DATA_DIR, "browser_profile")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                subprocess.Popen(
                    [browser, f"--app={url}", f"--user-data-dir={profile}",
                     "--no-first-run", "--no-default-browser-check"],
                    creationflags=flags,
                    close_fds=True,
                )
            except OSError:
                webbrowser.open(url)  # ブラウザが起動できない場合の保険
        else:
            webbrowser.open(url)


def open_or_focus(url=None):
    """管理画面を開く。既に開いていれば前面に持ってくるだけ(多重防止)。

    サーバーの応答待ちで呼び出し元(トレイメニュー等)が固まらないよう、
    実処理は別スレッドで行う。"""
    url = url or ADMIN_URL

    if sys.platform != "win32":
        webbrowser.open(url)
        return

    threading.Thread(target=_open_or_focus_blocking, args=(url,), daemon=True).start()
