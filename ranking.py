"""コメント数・キーワード・ビッツのランキング集計。PRIVMSGを受け取るたびに呼ばれる。"""

import datetime
import json
import threading

import db

_cache_lock = threading.Lock()
_keyword_cache = None  # [(id, label, [pattern, ...]), ...]


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def reload_keyword_cache():
    """設定画面でキーワードを追加・削除した後に呼ぶ。再起動不要にするため。"""
    global _keyword_cache
    rows = db.list_keyword_defs()
    cache = []
    for row in rows:
        try:
            patterns = json.loads(row["patterns"])
        except (ValueError, TypeError):
            patterns = []
        cache.append((row["id"], row["label"], [p.lower() for p in patterns if p]))
    with _cache_lock:
        _keyword_cache = cache


def _get_keyword_cache():
    if _keyword_cache is None:
        reload_keyword_cache()
    with _cache_lock:
        return _keyword_cache


def process_privmsg(parsed):
    login = parsed.login
    channel = parsed.channel
    if not login or not channel:
        return

    now = _now()

    db.record_message_daily(channel, login, now)

    text_lower = (parsed.text or "").lower()
    if text_lower:
        for keyword_id, label, patterns in _get_keyword_cache():
            if any(p in text_lower for p in patterns):
                db.record_keyword_hit(channel, keyword_id, login, now)

    bits_raw = parsed.tags.get("bits")
    if bits_raw and bits_raw.isdigit():
        db.record_bits(channel, login, int(bits_raw), now)
