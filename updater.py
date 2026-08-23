"""最新版の確認(GitHub Releases想定)。UPDATE_CHECK_URLが未設定なら何もしない。"""

import json
import threading
import time
import urllib.request

import config
import db
import state


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


def check_once():
    """1回だけ最新版を確認する。失敗しても例外を外に出さない(起動を邪魔しないため)。"""
    if not config.UPDATE_CHECK_URL:
        return
    if not db.is_update_check_enabled():
        return

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
    except Exception as e:
        print(f"[更新確認エラー] {e}")


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
