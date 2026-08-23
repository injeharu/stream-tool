"""SQLiteまわりの全処理をまとめたモジュール。IRCスレッドとFlask側の両方から呼ばれるためロックで直列化する。"""

import sqlite3
import threading
import datetime
import json

import config

_lock = threading.RLock()
_conn = None


def get_connection():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db():
    conn = get_connection()
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS viewers (
                login TEXT PRIMARY KEY,
                display_name TEXT,
                first_seen TEXT,
                last_seen TEXT,
                message_count INTEGER DEFAULT 0,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS sub_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT NOT NULL,
                event_type TEXT NOT NULL,
                cumulative_months INTEGER,
                streak_months INTEGER,
                tier TEXT,
                occurred_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sub_state (
                login TEXT PRIMARY KEY,
                display_name TEXT,
                cumulative_months INTEGER,
                streak_months INTEGER,
                tier TEXT,
                source TEXT NOT NULL DEFAULT 'chat',
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT NOT NULL,
                kind TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                reached_at TEXT NOT NULL,
                shipped INTEGER DEFAULT 0,
                shipped_at TEXT,
                dismissed INTEGER DEFAULT 0,
                memo TEXT,
                UNIQUE(login, kind, threshold)
            );

            CREATE TABLE IF NOT EXISTS keyword_defs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                patterns TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS keyword_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword_id INTEGER NOT NULL,
                login TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS message_daily (
                login TEXT NOT NULL,
                day TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (login, day)
            );

            CREATE TABLE IF NOT EXISTS bits_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT NOT NULL,
                bits INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        conn.commit()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# 同じ接続を複数スレッドで共有しているため、読み取りもロック内で行う
def _fetchall(sql, params=()):
    conn = get_connection()
    with _lock:
        return conn.execute(sql, params).fetchall()


def _fetchone(sql, params=()):
    conn = get_connection()
    with _lock:
        return conn.execute(sql, params).fetchone()


# ---------- viewers ----------

def upsert_viewer(login, display_name, seen_message=False):
    conn = get_connection()
    now = _now()
    with _lock:
        cur = conn.execute("SELECT login, message_count FROM viewers WHERE login=?", (login,))
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO viewers (login, display_name, first_seen, last_seen, message_count, note) "
                "VALUES (?, ?, ?, ?, ?, '')",
                (login, display_name, now, now, 1 if seen_message else 0),
            )
        else:
            if seen_message:
                conn.execute(
                    "UPDATE viewers SET display_name=?, last_seen=?, message_count=message_count+1 WHERE login=?",
                    (display_name, now, login),
                )
            else:
                conn.execute(
                    "UPDATE viewers SET display_name=? WHERE login=?",
                    (display_name, login),
                )
        conn.commit()


def get_viewer(login):
    return _fetchone("SELECT * FROM viewers WHERE login=?", (login,))


# ---------- sub_events / sub_state ----------

def record_sub_event(login, event_type, cumulative_months, streak_months, tier, occurred_at):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO sub_events (login, event_type, cumulative_months, streak_months, tier, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (login, event_type, cumulative_months, streak_months, tier, occurred_at),
        )
        conn.commit()


def get_sub_state(login):
    return _fetchone("SELECT * FROM sub_state WHERE login=?", (login,))


def upsert_sub_state(login, display_name, cumulative_months, streak_months, tier, source, updated_at=None):
    conn = get_connection()
    updated_at = updated_at or _now()
    with _lock:
        conn.execute(
            """
            INSERT INTO sub_state (login, display_name, cumulative_months, streak_months, tier, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(login) DO UPDATE SET
                display_name=excluded.display_name,
                cumulative_months=excluded.cumulative_months,
                streak_months=excluded.streak_months,
                tier=COALESCE(excluded.tier, sub_state.tier),
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (login, display_name, cumulative_months, streak_months, tier, source, updated_at),
        )
        conn.commit()


def list_all_sub_states():
    return _fetchall(
        """
        SELECT s.login, s.display_name, s.cumulative_months, s.streak_months, s.tier, s.source, s.updated_at
        FROM sub_state s
        ORDER BY s.cumulative_months DESC NULLS LAST, s.display_name COLLATE NOCASE ASC
        """
    )


# ---------- milestones ----------

def try_add_milestone(login, kind, threshold, reached_at):
    """新規登録できたらTrue、既にあった(=通知済み)ならFalseを返す。"""
    conn = get_connection()
    with _lock:
        try:
            conn.execute(
                "INSERT INTO milestones (login, kind, threshold, reached_at) VALUES (?, ?, ?, ?)",
                (login, kind, threshold, reached_at),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def list_pending_milestones():
    """発送待ち一覧。登録データは消さず、現在の設定(判定対象・発送対象ティア)で表示を絞る。"""
    rows = _fetchall(
        """
        SELECT m.id, m.login, v.display_name, m.kind, m.threshold, m.reached_at, s.tier
        FROM milestones m
        LEFT JOIN viewers v ON v.login = m.login
        LEFT JOIN sub_state s ON s.login = m.login
        WHERE m.shipped = 0 AND m.dismissed = 0
        ORDER BY m.threshold ASC, m.reached_at ASC
        """
    )
    eligible = get_eligible_tiers()
    return [
        r for r in rows
        if is_kind_enabled(r["kind"]) and (r["tier"] is None or r["tier"] in eligible)
    ]


def list_shipped_milestones():
    return _fetchall(
        """
        SELECT m.id, m.login, v.display_name, m.kind, m.threshold, m.reached_at, m.shipped_at, m.memo
        FROM milestones m
        LEFT JOIN viewers v ON v.login = m.login
        WHERE m.shipped = 1
        ORDER BY m.shipped_at DESC
        """
    )


def mark_shipped(milestone_id, memo=""):
    conn = get_connection()
    with _lock:
        conn.execute(
            "UPDATE milestones SET shipped=1, shipped_at=?, memo=? WHERE id=?",
            (_now(), memo, milestone_id),
        )
        conn.commit()


def dismiss_milestone(milestone_id):
    conn = get_connection()
    with _lock:
        conn.execute("UPDATE milestones SET dismissed=1 WHERE id=?", (milestone_id,))
        conn.commit()


def count_pending_milestones():
    # 表示フィルタ(判定対象・ティア)後の件数と一致させる
    return len(list_pending_milestones())


def count_milestones_reached_this_month():
    # 発送待ち一覧と同じ基準(判定対象・ティア)で絞る。発送済み・対応不要は到達実績として数える
    prefix = datetime.date.today().strftime("%Y-%m")
    rows = _fetchall(
        """
        SELECT m.kind, s.tier FROM milestones m
        LEFT JOIN sub_state s ON s.login = m.login
        WHERE m.reached_at LIKE ?
        """,
        (prefix + "%",),
    )
    eligible = get_eligible_tiers()
    return sum(
        1 for r in rows
        if is_kind_enabled(r["kind"]) and (r["tier"] is None or r["tier"] in eligible)
    )


def count_known_subscribers():
    return _fetchone("SELECT COUNT(*) AS c FROM sub_state")["c"]


# ---------- forecast ----------

def forecast_upcoming(months_ahead=3):
    """現在の通算/連続月数から、あと何ヶ月で各閾値に届くかを一覧化する。"""
    rows = _fetchall("SELECT login, display_name, cumulative_months, streak_months, tier FROM sub_state")

    results = []
    for row in rows:
        # 発送待ち一覧と同じ基準で、発送対象外ティアの人は表示しない
        if not is_tier_eligible(row["tier"]):
            continue
        for kind, current in (
            ("cumulative", row["cumulative_months"]),
            ("streak", row["streak_months"]),
        ):
            if current is None or not is_kind_enabled(kind):
                continue
            threshold = next_threshold_after(kind, current)
            if threshold is None:
                continue
            remaining = threshold - current
            if 0 < remaining <= months_ahead:
                results.append(
                    {
                        "login": row["login"],
                        "display_name": row["display_name"],
                        "kind": kind,
                        "threshold": threshold,
                        "current": current,
                        "remaining": remaining,
                    }
                )
    results.sort(key=lambda r: r["remaining"])
    return results


# ---------- settings ----------

def get_setting(key, default=None):
    row = _fetchone("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return default
    return row["value"]


def set_setting(key, value):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def get_thresholds(kind):
    key = "thresholds_cumulative" if kind == "cumulative" else "thresholds_streak"
    default = config.DEFAULT_CUMULATIVE_THRESHOLDS if kind == "cumulative" else config.DEFAULT_STREAK_THRESHOLDS
    raw = get_setting(key)
    if not raw:
        return list(default)
    try:
        return sorted(json.loads(raw))
    except (ValueError, TypeError):
        return list(default)


def set_thresholds(kind, thresholds):
    key = "thresholds_cumulative" if kind == "cumulative" else "thresholds_streak"
    set_setting(key, json.dumps(sorted(thresholds)))


def is_kind_enabled(kind):
    """通算(cumulative)/連続(streak)それぞれを閾値判定の対象にするか。デフォルトは両方ON。"""
    key = "kind_enabled_cumulative" if kind == "cumulative" else "kind_enabled_streak"
    return get_setting(key, "1") == "1"


def set_kind_enabled(kind, enabled):
    key = "kind_enabled_cumulative" if kind == "cumulative" else "kind_enabled_streak"
    set_setting(key, "1" if enabled else "0")


def get_interval(kind):
    """◯ヶ月ごとに繰り返す閾値の間隔。未設定/無効値ならNone。"""
    key = "interval_cumulative" if kind == "cumulative" else "interval_streak"
    raw = get_setting(key)
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


def set_interval(kind, value):
    key = "interval_cumulative" if kind == "cumulative" else "interval_streak"
    if value and int(value) > 0:
        set_setting(key, str(int(value)))
    else:
        set_setting(key, "")


def thresholds_up_to(kind, months):
    """固定閾値+繰り返し間隔の倍数のうち、months以下のものを昇順で返す。"""
    fixed = get_thresholds(kind)
    interval = get_interval(kind)
    result = set(t for t in fixed if t <= months)
    if interval:
        result.update(range(interval, months + 1, interval))
    return sorted(result)


def next_threshold_after(kind, months):
    """現在の月数より大きい直近の到達予定閾値(固定+繰り返し)を返す。無ければNone。"""
    fixed = get_thresholds(kind)
    interval = get_interval(kind)
    candidates = [t for t in fixed if t > months]
    if interval:
        candidates.append(((months // interval) + 1) * interval)
    if not candidates:
        return None
    return min(candidates)


def is_notify_enabled():
    return get_setting("notify_enabled", "1") == "1"


def is_update_check_enabled():
    return get_setting("update_check_enabled", "1") == "1"


def set_update_check_enabled(enabled):
    set_setting("update_check_enabled", "1" if enabled else "0")


def is_tutorial_seen():
    return get_setting("tutorial_seen", "0") == "1"


def mark_tutorial_seen():
    set_setting("tutorial_seen", "1")


def get_eligible_tiers():
    raw = get_setting("eligible_tiers")
    if not raw:
        return list(config.ALL_TIERS)
    try:
        return list(json.loads(raw))
    except (ValueError, TypeError):
        return list(config.ALL_TIERS)


def set_eligible_tiers(tiers):
    set_setting("eligible_tiers", json.dumps(list(tiers)))


def is_tier_eligible(tier):
    # ティア不明(手入力のみ等)は対象に含める。誤って発送対象から漏らすより安全なため。
    if tier is None:
        return True
    return tier in get_eligible_tiers()


# ---------- keyword ranking ----------

def list_keyword_defs():
    return _fetchall("SELECT id, label, patterns FROM keyword_defs ORDER BY id ASC")


def add_keyword_def(label, patterns):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO keyword_defs (label, patterns) VALUES (?, ?)",
            (label, json.dumps(patterns)),
        )
        conn.commit()


def delete_keyword_def(keyword_id):
    conn = get_connection()
    with _lock:
        conn.execute("DELETE FROM keyword_defs WHERE id=?", (keyword_id,))
        conn.execute("DELETE FROM keyword_hits WHERE keyword_id=?", (keyword_id,))
        conn.commit()


def record_keyword_hit(keyword_id, login, occurred_at):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO keyword_hits (keyword_id, login, occurred_at) VALUES (?, ?, ?)",
            (keyword_id, login, occurred_at),
        )
        conn.commit()


def _period_cutoff(period):
    now = datetime.datetime.now()
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if period == "week":
        return (now - datetime.timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    return None  # "all"


def keyword_ranking(period="all", keyword_id=None, limit=None):
    limit = limit or config.RANKING_TOP_N
    cutoff = _period_cutoff(period)

    sql = """
        SELECT h.login, v.display_name, COUNT(*) AS c
        FROM keyword_hits h
        LEFT JOIN viewers v ON v.login = h.login
        WHERE 1=1
    """
    params = []
    if keyword_id is not None:
        sql += " AND h.keyword_id = ?"
        params.append(keyword_id)
    if cutoff:
        sql += " AND h.occurred_at >= ?"
        params.append(cutoff)
    sql += " GROUP BY h.login ORDER BY c DESC LIMIT ?"
    params.append(limit)

    return _fetchall(sql, tuple(params))


# ---------- コメント数 / ビッツ 記録 ----------

def record_message_daily(login, occurred_at):
    conn = get_connection()
    day = occurred_at[:10]
    with _lock:
        conn.execute(
            """
            INSERT INTO message_daily (login, day, count) VALUES (?, ?, 1)
            ON CONFLICT(login, day) DO UPDATE SET count = count + 1
            """,
            (login, day),
        )
        conn.commit()


def message_ranking(period="all", limit=None):
    limit = limit or config.RANKING_TOP_N
    now = datetime.datetime.now()

    sql = """
        SELECT m.login, v.display_name, SUM(m.count) AS c
        FROM message_daily m
        LEFT JOIN viewers v ON v.login = m.login
        WHERE 1=1
    """
    params = []
    if period == "month":
        sql += " AND m.day >= ?"
        params.append(now.replace(day=1).strftime("%Y-%m-%d"))
    elif period == "week":
        sql += " AND m.day >= ?"
        params.append((now - datetime.timedelta(days=6)).strftime("%Y-%m-%d"))
    sql += " GROUP BY m.login ORDER BY c DESC LIMIT ?"
    params.append(limit)

    return _fetchall(sql, tuple(params))


def record_bits(login, bits, occurred_at):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO bits_events (login, bits, occurred_at) VALUES (?, ?, ?)",
            (login, bits, occurred_at),
        )
        conn.commit()


def bits_ranking(period="all", limit=None):
    limit = limit or config.RANKING_TOP_N
    cutoff = _period_cutoff(period)

    sql = """
        SELECT b.login, v.display_name, SUM(b.bits) AS c
        FROM bits_events b
        LEFT JOIN viewers v ON v.login = b.login
        WHERE 1=1
    """
    params = []
    if cutoff:
        sql += " AND b.occurred_at >= ?"
        params.append(cutoff)
    sql += " GROUP BY b.login ORDER BY c DESC LIMIT ?"
    params.append(limit)

    return _fetchall(sql, tuple(params))
