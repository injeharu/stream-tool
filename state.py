"""IRC接続状態をIRCスレッドとFlask側で共有するための小さなモジュール。"""

import threading

_lock = threading.Lock()
_status = "disconnected"  # connecting / connected / disconnected


def set_status(s):
    global _status
    with _lock:
        _status = s


def get_status():
    with _lock:
        return _status


# ---------- 更新チェック結果の共有 ----------

_update_lock = threading.Lock()
_available_update = None  # {"version": "1.1.0", "url": "https://..."} または None


def set_available_update(info):
    global _available_update
    with _update_lock:
        _available_update = info


def get_available_update():
    with _update_lock:
        return _available_update


# ---------- IRCクライアント参照(設定画面からのチャンネル即時切り替え用) ----------

_irc_client = None


def set_irc_client(client):
    global _irc_client
    _irc_client = client


def get_irc_client():
    return _irc_client
