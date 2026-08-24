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


# ---------- オーバーレイのテスト発火 ----------

import time as _time

_test_lock = threading.Lock()
_test_alert = None  # {"seq": 1, "name": "...", "kind": "通算", "threshold": 12, "fired_at": ...}
_test_seq = 0
_TEST_ALERT_TTL = 15  # 秒。期限切れ後は本物の到達を返す(テストが本番を隠し続けないように)


def fire_test_alert(name, kind, threshold):
    """OBSの見え方を確認するための疑似的な到達通知(DBには一切保存しない)。"""
    global _test_alert, _test_seq
    with _test_lock:
        _test_seq += 1
        _test_alert = {
            "seq": _test_seq,
            "name": name,
            "kind": kind,
            "threshold": threshold,
            "fired_at": _time.time(),
        }
        return _test_alert


def get_test_alert():
    with _test_lock:
        if _test_alert is None:
            return None
        if _time.time() - _test_alert["fired_at"] > _TEST_ALERT_TTL:
            return None
        return _test_alert
