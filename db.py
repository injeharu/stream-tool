"""SQLiteまわりの全処理をまとめたモジュール。IRCスレッドとFlask側の両方から呼ばれるためロックで直列化する。

チャンネルをまたいだデータ混在を避けるため、視聴者データ系のテーブルは channel 列で区切られている。
書き込み(IRCから来たイベント)はメッセージ自身が持つチャンネル名を使い、
読み取り(画面表示)は現在設定されているチャンネル(current_channel())を使う。
キーワード定義・閾値などの「ツールの設定」はチャンネルをまたいで共通。
"""

import os
import shutil
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
        # コメントが多いチャンネルでも書き込みが詰まらないようにする。
        # NORMALはWALと組み合わせる場合の推奨設定で、電源断でも
        # データベースは壊れない(直近数件の記録が失われる可能性があるだけ)。
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def _column_exists(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _table_exists(conn, table):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


NEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS viewers (
    channel TEXT NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT,
    custom_name TEXT,
    first_seen TEXT,
    last_seen TEXT,
    message_count INTEGER DEFAULT 0,
    note TEXT,
    PRIMARY KEY (channel, login)
);

CREATE TABLE IF NOT EXISTS sub_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT '',
    login TEXT NOT NULL,
    event_type TEXT NOT NULL,
    cumulative_months INTEGER,
    streak_months INTEGER,
    tier TEXT,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sub_state (
    channel TEXT NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT,
    cumulative_months INTEGER,
    streak_months INTEGER,
    tier TEXT,
    source TEXT NOT NULL DEFAULT 'chat',
    updated_at TEXT,
    base_months INTEGER,
    PRIMARY KEY (channel, login)
);

CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT '',
    login TEXT NOT NULL,
    kind TEXT NOT NULL,
    threshold INTEGER NOT NULL,
    reached_at TEXT NOT NULL,
    shipped INTEGER DEFAULT 0,
    shipped_at TEXT,
    dismissed INTEGER DEFAULT 0,
    memo TEXT,
    UNIQUE(channel, login, kind, threshold)
);

CREATE TABLE IF NOT EXISTS keyword_defs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    patterns TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keyword_hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT '',
    keyword_id INTEGER NOT NULL,
    login TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_daily (
    channel TEXT NOT NULL,
    login TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (channel, login, day)
);

CREATE TABLE IF NOT EXISTS bits_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT '',
    login TEXT NOT NULL,
    bits INTEGER NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gift_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL DEFAULT '',
    login TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    occurred_at TEXT NOT NULL
);

-- 導入前の実績などを手で足すための調整値。
-- 自動記録(gift_events/bits_events)とは別に持ち、
-- 表示は「自動記録 + 調整値」。こうすることで調整しても自動記録が壊れない。
CREATE TABLE IF NOT EXISTS manual_adjustments (
    channel TEXT NOT NULL,
    login TEXT NOT NULL,
    kind TEXT NOT NULL,          -- 'gift' or 'bits'
    amount INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (channel, login, kind)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Twitch連携で取得したサブスクライバー名簿。
-- チャットからは分からない「今サブスクしている人が誰か」を保持する。
-- 継続月数はTwitchのAPIに存在しないため、そちらは従来どおりチャットと手入力で管理する。
CREATE TABLE IF NOT EXISTS twitch_subscribers (
    channel TEXT NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT,
    tier TEXT,
    is_gift INTEGER DEFAULT 0,
    gifter_login TEXT,
    gifter_name TEXT,
    plan_name TEXT,
    synced_at TEXT,
    PRIMARY KEY (channel, login)
);

-- Twitch連携で取得したフォロワー一覧(フォロー日時つき)
CREATE TABLE IF NOT EXISTS twitch_followers (
    channel TEXT NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT,
    followed_at TEXT,
    synced_at TEXT,
    PRIMARY KEY (channel, login)
);

-- Twitch公式のビッツ支援ランキング。
-- チャット監視では起動中のCheerしか拾えないが、こちらは過去分を含む総額。
CREATE TABLE IF NOT EXISTS twitch_bits (
    channel TEXT NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT,
    rank INTEGER,
    score INTEGER,
    synced_at TEXT,
    PRIMARY KEY (channel, login)
);
"""


def _migrate_to_channel_scoped(conn):
    """channel列が無い旧DBを、チャンネル別に分離した新スキーマへ移行する。"""
    if not _table_exists(conn, "viewers") or _column_exists(conn, "viewers", "channel"):
        return  # 新規DB、または移行済み

    if os.path.exists(config.DB_PATH):
        backup_path = config.DB_PATH + ".bak"
        shutil.copyfile(config.DB_PATH, backup_path)
        print(f"[DB移行] 既存データを {backup_path} にバックアップしました。")

    row = conn.execute("SELECT value FROM settings WHERE key='channel_name'").fetchone()
    migrate_channel = row["value"] if row else ""
    print(f"[DB移行] 既存データをチャンネル「{migrate_channel}」に割り当てます。")

    # PRIMARY KEY/UNIQUE を変更するテーブルはリネーム→新規作成→コピーで移行する
    conn.execute("ALTER TABLE viewers RENAME TO viewers_old")
    conn.execute(
        """
        CREATE TABLE viewers (
            channel TEXT NOT NULL, login TEXT NOT NULL, display_name TEXT,
            first_seen TEXT, last_seen TEXT, message_count INTEGER DEFAULT 0, note TEXT,
            PRIMARY KEY (channel, login)
        )
        """
    )
    conn.execute(
        "INSERT INTO viewers (channel, login, display_name, first_seen, last_seen, message_count, note) "
        "SELECT ?, login, display_name, first_seen, last_seen, message_count, note FROM viewers_old",
        (migrate_channel,),
    )
    conn.execute("DROP TABLE viewers_old")

    conn.execute("ALTER TABLE sub_state RENAME TO sub_state_old")
    conn.execute(
        """
        CREATE TABLE sub_state (
            channel TEXT NOT NULL, login TEXT NOT NULL, display_name TEXT,
            cumulative_months INTEGER, streak_months INTEGER, tier TEXT,
            source TEXT NOT NULL DEFAULT 'chat', updated_at TEXT,
            PRIMARY KEY (channel, login)
        )
        """
    )
    conn.execute(
        "INSERT INTO sub_state (channel, login, display_name, cumulative_months, streak_months, tier, source, updated_at) "
        "SELECT ?, login, display_name, cumulative_months, streak_months, tier, source, updated_at FROM sub_state_old",
        (migrate_channel,),
    )
    conn.execute("DROP TABLE sub_state_old")

    conn.execute("ALTER TABLE milestones RENAME TO milestones_old")
    conn.execute(
        """
        CREATE TABLE milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL DEFAULT '', login TEXT NOT NULL,
            kind TEXT NOT NULL, threshold INTEGER NOT NULL, reached_at TEXT NOT NULL,
            shipped INTEGER DEFAULT 0, shipped_at TEXT, dismissed INTEGER DEFAULT 0, memo TEXT,
            UNIQUE(channel, login, kind, threshold)
        )
        """
    )
    conn.execute(
        "INSERT INTO milestones (channel, login, kind, threshold, reached_at, shipped, shipped_at, dismissed, memo) "
        "SELECT ?, login, kind, threshold, reached_at, shipped, shipped_at, dismissed, memo FROM milestones_old",
        (migrate_channel,),
    )
    conn.execute("DROP TABLE milestones_old")

    if _table_exists(conn, "message_daily"):
        conn.execute("ALTER TABLE message_daily RENAME TO message_daily_old")
        conn.execute(
            """
            CREATE TABLE message_daily (
                channel TEXT NOT NULL, login TEXT NOT NULL, day TEXT NOT NULL, count INTEGER DEFAULT 0,
                PRIMARY KEY (channel, login, day)
            )
            """
        )
        conn.execute(
            "INSERT INTO message_daily (channel, login, day, count) SELECT ?, login, day, count FROM message_daily_old",
            (migrate_channel,),
        )
        conn.execute("DROP TABLE message_daily_old")

    # channel列を追加するだけで済むテーブル(UNIQUE制約が絡まないもの)
    for table in ("sub_events", "keyword_hits", "bits_events"):
        if _table_exists(conn, table) and not _column_exists(conn, table, "channel"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN channel TEXT NOT NULL DEFAULT ''")
            conn.execute(f"UPDATE {table} SET channel=? WHERE channel=''", (migrate_channel,))

    conn.commit()
    print("[DB移行] 完了しました。")


def init_db():
    conn = get_connection()
    with _lock:
        _migrate_to_channel_scoped(conn)
        conn.executescript(NEW_SCHEMA)
        # 旧DBへの列追加(カスタム名: 手動で付けた名前はチャットで上書きされない)
        if not _column_exists(conn, "viewers", "custom_name"):
            conn.execute("ALTER TABLE viewers ADD COLUMN custom_name TEXT")
        # 旧DBへの列追加(数え方モード用: その人を初めて見かけた時点の通算月数)
        if not _column_exists(conn, "sub_state", "base_months"):
            conn.execute("ALTER TABLE sub_state ADD COLUMN base_months INTEGER")
        conn.commit()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def current_channel():
    """画面表示・手動編集の対象とする「現在設定されているチャンネル」。"""
    return get_setting("channel_name", "") or ""


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
# 書き込み系は引数で明示的にchannelを受け取る(IRCメッセージ自身のチャンネルを使うため)。

def upsert_viewer(channel, login, display_name, seen_message=False):
    conn = get_connection()
    now = _now()
    with _lock:
        cur = conn.execute("SELECT login FROM viewers WHERE channel=? AND login=?", (channel, login))
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO viewers (channel, login, display_name, first_seen, last_seen, message_count, note) "
                "VALUES (?, ?, ?, ?, ?, ?, '')",
                (channel, login, display_name, now, now, 1 if seen_message else 0),
            )
        else:
            if seen_message:
                conn.execute(
                    "UPDATE viewers SET display_name=?, last_seen=?, message_count=message_count+1 "
                    "WHERE channel=? AND login=?",
                    (display_name, now, channel, login),
                )
            else:
                conn.execute(
                    "UPDATE viewers SET display_name=? WHERE channel=? AND login=?",
                    (display_name, channel, login),
                )
        conn.commit()


def get_viewer(channel, login):
    return _fetchone("SELECT * FROM viewers WHERE channel=? AND login=?", (channel, login))


# ---------- sub_events / sub_state ----------

def record_sub_event(channel, login, event_type, cumulative_months, streak_months, tier, occurred_at):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO sub_events (channel, login, event_type, cumulative_months, streak_months, tier, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (channel, login, event_type, cumulative_months, streak_months, tier, occurred_at),
        )
        conn.commit()


def get_sub_state(channel, login):
    return _fetchone("SELECT * FROM sub_state WHERE channel=? AND login=?", (channel, login))


def upsert_sub_state(channel, login, display_name, cumulative_months, streak_months, tier, source, updated_at=None):
    conn = get_connection()
    updated_at = updated_at or _now()
    with _lock:
        # base_months(初めて見かけた時点の月数)は新規登録時のみ記録し、以後は変えない
        conn.execute(
            """
            INSERT INTO sub_state (channel, login, display_name, cumulative_months, streak_months, tier, source, updated_at, base_months)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel, login) DO UPDATE SET
                display_name=excluded.display_name,
                cumulative_months=excluded.cumulative_months,
                streak_months=excluded.streak_months,
                tier=COALESCE(excluded.tier, sub_state.tier),
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (channel, login, display_name, cumulative_months, streak_months, tier, source, updated_at, cumulative_months),
        )
        conn.commit()


def list_all_sub_states(limit=None, offset=0):
    # Twitch連携している場合、公式名簿に載っているかどうかを併記する
    # (記録そのものは変えず、確認済みの印とギフト情報を添えるだけ)
    sql = """
        SELECT s.login, COALESCE(v.custom_name, s.display_name) AS display_name,
               s.cumulative_months, s.streak_months, s.tier, s.source, s.updated_at,
               CASE WHEN t.login IS NOT NULL THEN 1 ELSE 0 END AS twitch_verified,
               t.tier AS twitch_tier, t.is_gift AS twitch_is_gift, t.gifter_name AS twitch_gifter
        FROM sub_state s
        LEFT JOIN viewers v ON v.channel = s.channel AND v.login = s.login
        LEFT JOIN twitch_subscribers t ON t.channel = s.channel AND t.login = s.login
        WHERE s.channel = ?
        ORDER BY s.cumulative_months DESC NULLS LAST, s.display_name COLLATE NOCASE ASC
    """
    params = [current_channel()]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return _fetchall(sql, tuple(params))


def count_all_sub_states():
    return _fetchone("SELECT COUNT(*) AS c FROM sub_state WHERE channel=?", (current_channel(),))["c"]


# 削除対象は「活動記録」のみ(viewers/sub_state/コメント・キーワード・ビッツ集計)。
# sub_events(生ログ)とmilestones(特典履歴)は監査証跡として残す。
_DELETABLE_TABLES = ("viewers", "sub_state", "message_daily", "keyword_hits", "bits_events")


def delete_viewer(channel, login):
    conn = get_connection()
    with _lock:
        for table in _DELETABLE_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE channel=? AND login=?", (channel, login))
        # 未対応の特典(特典待ち・対応不要)も一緒に消す。特典済みの履歴だけ実績として残す
        conn.execute(
            "DELETE FROM milestones WHERE channel=? AND login=? AND shipped=0",
            (channel, login),
        )
        conn.execute(
            "DELETE FROM gift_events WHERE channel=? AND login=?",
            (channel, login),
        )
        conn.commit()


def rename_viewer(channel, login, new_display_name):
    """手動で付けた名前(custom_name)として保存する。チャット由来の表示名更新では上書きされない。"""
    conn = get_connection()
    now = _now()
    with _lock:
        conn.execute(
            """
            INSERT INTO viewers (channel, login, display_name, custom_name, first_seen, last_seen, message_count, note)
            VALUES (?, ?, ?, ?, ?, ?, 0, '')
            ON CONFLICT(channel, login) DO UPDATE SET custom_name=excluded.custom_name
            """,
            (channel, login, login, new_display_name, now, now),
        )
        conn.commit()


# ---------- milestones ----------

def try_add_milestone(channel, login, kind, threshold, reached_at):
    """新規登録できたらTrue、既にあった(=通知済み)ならFalseを返す。"""
    conn = get_connection()
    with _lock:
        try:
            conn.execute(
                "INSERT INTO milestones (channel, login, kind, threshold, reached_at) VALUES (?, ?, ?, ?, ?)",
                (channel, login, kind, threshold, reached_at),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def list_pending_milestones():
    """特典待ち一覧。登録データは消さず、現在の設定(判定対象・特典対象ティア)で表示を絞る。"""
    channel = current_channel()
    rows = _fetchall(
        """
        SELECT m.id, m.login, COALESCE(v.custom_name, v.display_name) AS display_name,
               m.kind, m.threshold, m.reached_at, s.tier
        FROM milestones m
        LEFT JOIN viewers v ON v.channel = m.channel AND v.login = m.login
        LEFT JOIN sub_state s ON s.channel = m.channel AND s.login = m.login
        WHERE m.channel = ? AND m.shipped = 0 AND m.dismissed = 0
        ORDER BY m.threshold ASC, m.reached_at ASC
        """,
        (channel,),
    )
    eligible = get_eligible_tiers()
    return [
        r for r in rows
        if is_kind_enabled(r["kind"]) and (r["tier"] is None or r["tier"] in eligible)
    ]


def list_shipped_milestones():
    return _fetchall(
        """
        SELECT m.id, m.login, COALESCE(v.custom_name, v.display_name) AS display_name,
               m.kind, m.threshold, m.reached_at, m.shipped_at, m.memo
        FROM milestones m
        LEFT JOIN viewers v ON v.channel = m.channel AND v.login = m.login
        WHERE m.channel = ? AND m.shipped = 1
        ORDER BY m.shipped_at DESC
        """,
        (current_channel(),),
    )


def list_dismissed_milestones():
    """「対応不要」にした項目の一覧(復元用)。"""
    return _fetchall(
        """
        SELECT m.id, m.login, COALESCE(v.custom_name, v.display_name) AS display_name,
               m.kind, m.threshold, m.reached_at
        FROM milestones m
        LEFT JOIN viewers v ON v.channel = m.channel AND v.login = m.login
        WHERE m.channel = ? AND m.dismissed = 1
        ORDER BY m.reached_at DESC
        """,
        (current_channel(),),
    )


def latest_milestone():
    """最新の到達1件(OBSの祝福オーバーレイ用)。"""
    return _fetchone(
        """
        SELECT m.id, m.login, COALESCE(v.custom_name, v.display_name) AS display_name,
               m.kind, m.threshold
        FROM milestones m
        LEFT JOIN viewers v ON v.channel = m.channel AND v.login = m.login
        WHERE m.channel = ?
        ORDER BY m.id DESC LIMIT 1
        """,
        (current_channel(),),
    )


def restore_milestone(milestone_id):
    """「対応不要」を取り消して特典待ちに戻す。"""
    conn = get_connection()
    with _lock:
        conn.execute("UPDATE milestones SET dismissed=0 WHERE id=?", (milestone_id,))
        conn.commit()


def unship_milestone(milestone_id):
    """「特典済み」を取り消して特典待ちに戻す(押し間違いの復旧用。メモは残す)。"""
    conn = get_connection()
    with _lock:
        conn.execute(
            "UPDATE milestones SET shipped=0, shipped_at=NULL WHERE id=?",
            (milestone_id,),
        )
        conn.commit()


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
    # 特典待ち一覧と同じ基準(判定対象・ティア)で絞る。特典済み・対応不要は到達実績として数える
    prefix = datetime.date.today().strftime("%Y-%m")
    rows = _fetchall(
        """
        SELECT m.kind, s.tier FROM milestones m
        LEFT JOIN sub_state s ON s.channel = m.channel AND s.login = m.login
        WHERE m.channel = ? AND m.reached_at LIKE ?
        """,
        (current_channel(), prefix + "%"),
    )
    eligible = get_eligible_tiers()
    return sum(
        1 for r in rows
        if is_kind_enabled(r["kind"]) and (r["tier"] is None or r["tier"] in eligible)
    )


def count_known_subscribers():
    return _fetchone("SELECT COUNT(*) AS c FROM sub_state WHERE channel=?", (current_channel(),))["c"]


# ---------- forecast ----------

def forecast_upcoming(limit=100):
    """各視聴者の「次に到達する閾値」を近い順に一覧化する(何ヶ月先でも表示)。"""
    rows = _fetchall(
        """
        SELECT s.login, COALESCE(v.custom_name, s.display_name) AS display_name,
               s.cumulative_months, s.streak_months, s.tier, s.base_months
        FROM sub_state s
        LEFT JOIN viewers v ON v.channel = s.channel AND v.login = s.login
        WHERE s.channel=?
        """,
        (current_channel(),),
    )

    results = []
    for row in rows:
        # 特典待ち一覧と同じ基準で、特典対象外ティアの人は表示しない
        if not is_tier_eligible(row["tier"]):
            continue
        for kind, current in (
            ("cumulative", effective_cumulative(row["cumulative_months"], row["base_months"])),
            ("streak", row["streak_months"]),
        ):
            if current is None or not is_kind_enabled(kind):
                continue
            threshold = next_threshold_after(kind, current)
            if threshold is None:
                continue
            remaining = threshold - current
            if remaining > 0:
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
    results.sort(key=lambda r: (r["remaining"], -r["current"]))
    return results[:limit]


# ---------- settings(チャンネル共通) ----------

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


# ---------- 数え方モード ----------
# absolute      : 通算月数そのもので判定(6ヶ月なら通算6ヶ月ちょうどで到達)
# since_install : その人を初めて見かけた時点からの「増加分」で判定
#                 (例: 57ヶ月の人は+6ヶ月後=通算63ヶ月で最初の特典。導入時に過去分が積まれない)

def get_count_mode():
    mode = get_setting("count_mode", "absolute")
    return mode if mode in ("absolute", "since_install") else "absolute"


def set_count_mode(mode):
    if mode not in ("absolute", "since_install"):
        return
    previous = get_count_mode()
    set_setting("count_mode", mode)
    if mode == "since_install" and previous != "since_install":
        # 切替時点を「今日」として全員の基準月数を現在値に揃える
        conn = get_connection()
        with _lock:
            conn.execute("UPDATE sub_state SET base_months = COALESCE(cumulative_months, 0)")
            conn.commit()


def effective_cumulative(cumulative_months, base_months):
    """数え方モードを反映した判定用の通算月数を返す。"""
    if cumulative_months is None:
        return None
    if get_count_mode() == "since_install":
        return max(cumulative_months - (base_months or 0), 0)
    return cumulative_months


def is_notify_enabled():
    return get_setting("notify_enabled", "1") == "1"


# ---------- Twitch連携で取得したデータ ----------

def replace_twitch_subscribers(channel, rows):
    """サブスクライバー名簿を丸ごと入れ替える(解約した人が残らないように)。"""
    conn = get_connection()
    now = _now()
    with _lock:
        conn.execute("DELETE FROM twitch_subscribers WHERE channel=?", (channel,))
        conn.executemany(
            """
            INSERT INTO twitch_subscribers
                (channel, login, display_name, tier, is_gift, gifter_login, gifter_name, plan_name, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    channel,
                    (r.get("user_login") or "").lower(),
                    r.get("user_name") or r.get("user_login"),
                    r.get("tier"),
                    1 if r.get("is_gift") else 0,
                    (r.get("gifter_login") or "").lower(),
                    r.get("gifter_name") or "",
                    r.get("plan_name") or "",
                    now,
                )
                for r in rows
                if r.get("user_login")
            ],
        )
        conn.commit()
    set_setting("twitch_subs_synced_at", now)


def replace_twitch_followers(channel, rows):
    conn = get_connection()
    now = _now()
    with _lock:
        conn.execute("DELETE FROM twitch_followers WHERE channel=?", (channel,))
        conn.executemany(
            """
            INSERT INTO twitch_followers (channel, login, display_name, followed_at, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    channel,
                    (r.get("user_login") or "").lower(),
                    r.get("user_name") or r.get("user_login"),
                    r.get("followed_at") or "",
                    now,
                )
                for r in rows
                if r.get("user_login")
            ],
        )
        conn.commit()
    set_setting("twitch_followers_synced_at", now)


def replace_twitch_bits(channel, rows):
    conn = get_connection()
    now = _now()
    with _lock:
        conn.execute("DELETE FROM twitch_bits WHERE channel=?", (channel,))
        conn.executemany(
            """
            INSERT INTO twitch_bits (channel, login, display_name, rank, score, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    channel,
                    (r.get("user_login") or "").lower(),
                    r.get("user_name") or r.get("user_login"),
                    r.get("rank"),
                    r.get("score"),
                    now,
                )
                for r in rows
                if r.get("user_login")
            ],
        )
        conn.commit()
    set_setting("twitch_bits_synced_at", now)


def list_twitch_bits(limit=None, offset=0):
    sql = """
        SELECT b.login, COALESCE(v.custom_name, b.display_name) AS display_name,
               b.rank, b.score
        FROM twitch_bits b
        LEFT JOIN viewers v ON v.channel = b.channel AND v.login = b.login
        WHERE b.channel = ?
        ORDER BY b.rank ASC
    """
    params = [current_channel()]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return _fetchall(sql, tuple(params))


def count_twitch_bits():
    return _fetchone(
        "SELECT COUNT(*) AS c FROM twitch_bits WHERE channel=?", (current_channel(),)
    )["c"]


def list_twitch_followers(limit=None, offset=0):
    sql = """
        SELECT f.login, COALESCE(v.custom_name, f.display_name) AS display_name,
               f.followed_at,
               CASE WHEN s.login IS NOT NULL THEN 1 ELSE 0 END AS is_subscriber
        FROM twitch_followers f
        LEFT JOIN viewers v ON v.channel = f.channel AND v.login = f.login
        LEFT JOIN twitch_subscribers s ON s.channel = f.channel AND s.login = f.login
        WHERE f.channel = ?
        ORDER BY f.followed_at DESC
    """
    params = [current_channel()]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return _fetchall(sql, tuple(params))


def get_twitch_goals():
    raw = get_setting("twitch_goals", "")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []


def list_twitch_subscribers(limit=None, offset=0):
    """連携で取得した名簿。チャットから分かっている月数も一緒に表示する。"""
    sql = """
        SELECT s.login,
               COALESCE(v.custom_name, s.display_name) AS display_name,
               s.tier, s.is_gift, s.gifter_name, s.plan_name,
               st.cumulative_months, st.streak_months,
               f.followed_at
        FROM twitch_subscribers s
        LEFT JOIN viewers v ON v.channel = s.channel AND v.login = s.login
        LEFT JOIN sub_state st ON st.channel = s.channel AND st.login = s.login
        LEFT JOIN twitch_followers f ON f.channel = s.channel AND f.login = s.login
        WHERE s.channel = ?
        ORDER BY st.cumulative_months DESC NULLS LAST, s.tier DESC, s.display_name COLLATE NOCASE
    """
    params = [current_channel()]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return _fetchall(sql, tuple(params))


def count_twitch_subscribers():
    return _fetchone(
        "SELECT COUNT(*) AS c FROM twitch_subscribers WHERE channel=?", (current_channel(),)
    )["c"]


def twitch_subscriber_stats():
    """名簿の内訳(ティア別・ギフト数)。"""
    channel = current_channel()
    rows = _fetchall(
        "SELECT tier, COUNT(*) AS c FROM twitch_subscribers WHERE channel=? GROUP BY tier",
        (channel,),
    )
    gifts = _fetchone(
        "SELECT COUNT(*) AS c FROM twitch_subscribers WHERE channel=? AND is_gift=1", (channel,)
    )["c"]
    # 月数が分かっていない人(チャットで共有していない人)の数
    unknown = _fetchone(
        """
        SELECT COUNT(*) AS c FROM twitch_subscribers s
        LEFT JOIN sub_state st ON st.channel = s.channel AND st.login = s.login
        WHERE s.channel = ? AND st.cumulative_months IS NULL
        """,
        (channel,),
    )["c"]
    return {
        "by_tier": {r["tier"]: r["c"] for r in rows},
        "gift_count": gifts,
        "unknown_months": unknown,
        "total": count_twitch_subscribers(),
    }


def count_twitch_followers():
    return _fetchone(
        "SELECT COUNT(*) AS c FROM twitch_followers WHERE channel=?", (current_channel(),)
    )["c"]


def is_shared_chat_ignored():
    """統合チャット(コラボ配信)で流れてくる他チャンネルのメッセージを無視するか。
    既定はON(自分のチャンネルの記録に他所の視聴者が混ざらないようにする)。"""
    return get_setting("ignore_shared_chat", "1") == "1"


def set_shared_chat_ignored(enabled):
    set_setting("ignore_shared_chat", "1" if enabled else "0")


def get_custom_sound():
    """オーバーレイの効果音として登録されたファイル名。未設定なら空文字(内蔵音を使う)。"""
    return get_setting("custom_sound", "") or ""


def set_custom_sound(filename):
    set_setting("custom_sound", filename or "")


def get_sound_volume():
    """効果音の音量(0〜100)。既定は70。"""
    raw = get_setting("sound_volume", "70")
    try:
        return max(0, min(100, int(raw)))
    except (TypeError, ValueError):
        return 70


def set_sound_volume(value):
    try:
        value = max(0, min(100, int(value)))
    except (TypeError, ValueError):
        value = 70
    set_setting("sound_volume", str(value))


def is_notify_persistent():
    """通知を「閉じるまで消さない」で出すか。既定はON(見逃し防止)。"""
    return get_setting("notify_persistent", "1") == "1"


def set_notify_persistent(enabled):
    set_setting("notify_persistent", "1" if enabled else "0")


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
    # ティア不明(手入力のみ等)は対象に含める。誤って特典対象から漏らすより安全なため。
    if tier is None:
        return True
    return tier in get_eligible_tiers()


# ---------- keyword ranking ----------
# keyword_defs(定義)はチャンネル共通。keyword_hits(集計)はチャンネル別。

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


def record_keyword_hit(channel, keyword_id, login, occurred_at):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO keyword_hits (channel, keyword_id, login, occurred_at) VALUES (?, ?, ?, ?)",
            (channel, keyword_id, login, occurred_at),
        )
        conn.commit()


def _period_cutoff(period):
    now = datetime.datetime.now()
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if period == "week":
        return (now - datetime.timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    return None  # "all"


def keyword_ranking(period="all", keyword_id=None, limit=None, offset=0):
    limit = limit or config.RANKING_TOP_N
    cutoff = _period_cutoff(period)

    sql = """
        SELECT h.login, COALESCE(v.custom_name, v.display_name) AS display_name, COUNT(*) AS c
        FROM keyword_hits h
        LEFT JOIN viewers v ON v.channel = h.channel AND v.login = h.login
        WHERE h.channel = ?
    """
    params = [current_channel()]
    if keyword_id is not None:
        sql += " AND h.keyword_id = ?"
        params.append(keyword_id)
    if cutoff:
        sql += " AND h.occurred_at >= ?"
        params.append(cutoff)
    sql += " GROUP BY h.login ORDER BY c DESC LIMIT ? OFFSET ?"
    params.append(limit)
    params.append(offset)

    return _fetchall(sql, tuple(params))


def keyword_ranking_count(period="all", keyword_id=None):
    """該当する視聴者数(ページ送り用)。"""
    cutoff = _period_cutoff(period)
    sql = "SELECT COUNT(DISTINCT h.login) AS c FROM keyword_hits h WHERE h.channel = ?"
    params = [current_channel()]
    if keyword_id is not None:
        sql += " AND h.keyword_id = ?"
        params.append(keyword_id)
    if cutoff:
        sql += " AND h.occurred_at >= ?"
        params.append(cutoff)
    return _fetchone(sql, tuple(params))["c"]


# ---------- コメント数 / ビッツ 記録 ----------

def record_message_daily(channel, login, occurred_at):
    conn = get_connection()
    day = occurred_at[:10]
    with _lock:
        conn.execute(
            """
            INSERT INTO message_daily (channel, login, day, count) VALUES (?, ?, ?, 1)
            ON CONFLICT(channel, login, day) DO UPDATE SET count = count + 1
            """,
            (channel, login, day),
        )
        conn.commit()


def _message_period_filter(period):
    now = datetime.datetime.now()
    if period == "month":
        return " AND m.day >= ?", now.replace(day=1).strftime("%Y-%m-%d")
    if period == "week":
        return " AND m.day >= ?", (now - datetime.timedelta(days=6)).strftime("%Y-%m-%d")
    return "", None


def message_ranking(period="all", limit=None, offset=0):
    limit = limit or config.RANKING_TOP_N
    clause, value = _message_period_filter(period)

    sql = """
        SELECT m.login, COALESCE(v.custom_name, v.display_name) AS display_name, SUM(m.count) AS c
        FROM message_daily m
        LEFT JOIN viewers v ON v.channel = m.channel AND v.login = m.login
        WHERE m.channel = ?
    """
    params = [current_channel()]
    if value is not None:
        sql += clause
        params.append(value)
    sql += " GROUP BY m.login ORDER BY c DESC LIMIT ? OFFSET ?"
    params.append(limit)
    params.append(offset)

    return _fetchall(sql, tuple(params))


def message_ranking_count(period="all"):
    clause, value = _message_period_filter(period)
    sql = "SELECT COUNT(DISTINCT m.login) AS c FROM message_daily m WHERE m.channel = ?"
    params = [current_channel()]
    if value is not None:
        sql += clause
        params.append(value)
    return _fetchone(sql, tuple(params))["c"]


def record_bits(channel, login, bits, occurred_at):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO bits_events (channel, login, bits, occurred_at) VALUES (?, ?, ?, ?)",
            (channel, login, bits, occurred_at),
        )
        conn.commit()


def _event_ranking(table, value_column, kind, period, limit, offset):
    """ビッツ・ギフトのランキング。累計表示のときだけ手入力の調整値を合算する。
    (期間を絞ったときは「その期間の実績」を見たいので調整値は含めない)"""
    limit = limit or config.RANKING_TOP_N
    cutoff = _period_cutoff(period)
    channel = current_channel()
    include_manual = cutoff is None

    sql = f"""
        SELECT e.login,
               COALESCE(v.custom_name, v.display_name) AS display_name,
               SUM(e.{value_column}) AS auto_total,
               {"COALESCE(m.amount, 0)" if include_manual else "0"} AS manual_total
        FROM {table} e
        LEFT JOIN viewers v ON v.channel = e.channel AND v.login = e.login
        LEFT JOIN manual_adjustments m
               ON m.channel = e.channel AND m.login = e.login AND m.kind = ?
        WHERE e.channel = ?
    """
    params = [kind, channel]
    if cutoff:
        sql += " AND e.occurred_at >= ?"
        params.append(cutoff)
    sql += " GROUP BY e.login"

    if include_manual:
        # 自動記録が無く調整値だけの人も一覧に出す
        sql += f"""
        UNION ALL
        SELECT m.login,
               COALESCE(v.custom_name, v.display_name) AS display_name,
               0 AS auto_total,
               m.amount AS manual_total
        FROM manual_adjustments m
        LEFT JOIN viewers v ON v.channel = m.channel AND v.login = m.login
        WHERE m.channel = ? AND m.kind = ? AND m.amount <> 0
          AND NOT EXISTS (
              SELECT 1 FROM {table} e2 WHERE e2.channel = m.channel AND e2.login = m.login
          )
        """
        params += [channel, kind]

    outer = f"""
        SELECT login, display_name, auto_total, manual_total,
               (auto_total + manual_total) AS c
        FROM ({sql})
        ORDER BY c DESC LIMIT ? OFFSET ?
    """
    params += [limit, offset]
    return _fetchall(outer, tuple(params))


def _event_ranking_count(table, kind, period):
    cutoff = _period_cutoff(period)
    channel = current_channel()
    sql = f"SELECT COUNT(DISTINCT login) AS c FROM (SELECT login FROM {table} WHERE channel = ?"
    params = [channel]
    if cutoff:
        sql += " AND occurred_at >= ?"
        params.append(cutoff)
    else:
        sql += (
            " UNION SELECT login FROM manual_adjustments"
            " WHERE channel = ? AND kind = ? AND amount <> 0"
        )
        params += [channel, kind]
    sql += ")"
    return _fetchone(sql, tuple(params))["c"]


def bits_ranking(period="all", limit=None, offset=0):
    return _event_ranking("bits_events", "bits", "bits", period, limit, offset)


def bits_ranking_count(period="all"):
    return _event_ranking_count("bits_events", "bits", period)


# ---------- 手入力の調整値 ----------

def get_adjustment(channel, login, kind):
    row = _fetchone(
        "SELECT amount FROM manual_adjustments WHERE channel=? AND login=? AND kind=?",
        (channel, login, kind),
    )
    return row["amount"] if row else 0


def set_adjustment(channel, login, kind, amount):
    """手入力の調整値を設定する。0なら記録ごと削除する。"""
    conn = get_connection()
    with _lock:
        if amount:
            conn.execute(
                """
                INSERT INTO manual_adjustments (channel, login, kind, amount, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel, login, kind) DO UPDATE SET
                    amount=excluded.amount, updated_at=excluded.updated_at
                """,
                (channel, login, kind, int(amount), _now()),
            )
        else:
            conn.execute(
                "DELETE FROM manual_adjustments WHERE channel=? AND login=? AND kind=?",
                (channel, login, kind),
            )
        conn.commit()


def get_auto_total(channel, login, kind):
    """自動記録された分の合計(調整値を除く)。"""
    table, column = ("gift_events", "count") if kind == "gift" else ("bits_events", "bits")
    row = _fetchone(
        f"SELECT COALESCE(SUM({column}), 0) AS c FROM {table} WHERE channel=? AND login=?",
        (channel, login),
    )
    return row["c"] if row else 0


# ---------- ギフトサブ(贈り主) ----------

def record_gift(channel, login, count, occurred_at):
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO gift_events (channel, login, count, occurred_at) VALUES (?, ?, ?, ?)",
            (channel, login, count, occurred_at),
        )
        conn.commit()


def gift_ranking(period="all", limit=None, offset=0):
    return _event_ranking("gift_events", "count", "gift", period, limit, offset)


def gift_ranking_count(period="all"):
    return _event_ranking_count("gift_events", "gift", period)


# ---------- チャンネル名の正規化 ----------

def normalize_channel(raw):
    """URL貼り付けや大文字・記号混じりの入力からTwitchのログイン名を取り出す。
    無効な入力は空文字を返す(保存しない判断に使う)。"""
    import re

    s = (raw or "").strip().lower()
    if "twitch.tv" in s:
        s = s.split("twitch.tv", 1)[1].lstrip("/")
    s = s.lstrip("@#").split("/")[0].split("?")[0].strip()
    if re.fullmatch(r"[a-z0-9_]{1,25}", s):
        return s
    return ""
