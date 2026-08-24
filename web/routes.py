"""管理画面のFlaskルート定義。"""

import csv
import io
import json

from urllib.parse import urlparse

from flask import Flask, Blueprint, render_template, request, redirect, url_for, Response, abort, jsonify

import autostart
import config
import db
import state
import milestone
import ranking

bp = Blueprint("main", __name__)

_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


@bp.before_request
def verify_local_request():
    """外部サイトからの遠隔操作(CSRF)やDNSリバインディング対策。
    ローカル(127.0.0.1)以外を名乗るリクエストと、外部ページ発のPOSTを拒否する。"""
    host = (request.host or "").split(":")[0].lower()
    if host not in _LOCAL_HOSTS:
        abort(403)
    origin = request.headers.get("Origin")
    if origin:
        origin_host = (urlparse(origin).hostname or "").lower()
        if origin_host not in _LOCAL_HOSTS:
            abort(403)


@bp.context_processor
def inject_status():
    return {
        "irc_status": state.get_status(),
        "available_update": state.get_available_update(),
        "app_version": config.APP_VERSION,
        "tutorial_seen": db.is_tutorial_seen(),
        "current_channel": db.current_channel(),
    }


@bp.route("/tutorial/seen", methods=["POST"])
def tutorial_seen():
    db.mark_tutorial_seen()
    return ("", 204)


@bp.route("/")
def index():
    all_pending = db.list_pending_milestones()

    # 閾値フィルタ(上部ボタン)。存在する閾値だけボタンにする
    thresholds = sorted({r["threshold"] for r in all_pending})
    selected = request.args.get("threshold", type=int)
    if selected is not None and selected in thresholds:
        pending = [r for r in all_pending if r["threshold"] == selected]
    else:
        selected = None
        pending = all_pending

    # 同じ人の複数到達を1枚のカードにまとめる
    by_login = {}
    for r in pending:
        g = by_login.get(r["login"])
        if g is None:
            g = {"login": r["login"], "display_name": r["display_name"], "items": []}
            by_login[r["login"]] = g
        g["items"].append(r)

    # カードの並びは名前順で固定(到達のたびに順番が入れ替わらないように)
    groups = sorted(
        by_login.values(),
        key=lambda g: (g["display_name"] or g["login"]).casefold(),
    )
    for g in groups:
        g["items"].sort(key=lambda r: r["threshold"])

    tiles = {
        "pending_count": len(all_pending),
        "pending_people": len({r["login"] for r in all_pending}),
        "reached_this_month": db.count_milestones_reached_this_month(),
        "known_subscribers": db.count_known_subscribers(),
    }
    return render_template(
        "index.html",
        groups=groups,
        tiles=tiles,
        thresholds=thresholds,
        selected_threshold=selected,
        active="index",
    )


@bp.route("/milestones/<int:milestone_id>/ship", methods=["POST"])
def ship_milestone(milestone_id):
    memo = request.form.get("memo", "").strip()
    db.mark_shipped(milestone_id, memo)
    return redirect(url_for("main.index"))


@bp.route("/milestones/<int:milestone_id>/dismiss", methods=["POST"])
def dismiss_milestone_route(milestone_id):
    db.dismiss_milestone(milestone_id)
    return redirect(url_for("main.index"))


@bp.route("/history")
def history():
    shipped = db.list_shipped_milestones()
    dismissed = db.list_dismissed_milestones()
    return render_template("history.html", shipped=shipped, dismissed=dismissed, active="history")


@bp.route("/milestones/<int:milestone_id>/restore", methods=["POST"])
def restore_milestone_route(milestone_id):
    db.restore_milestone(milestone_id)
    return redirect(url_for("main.history"))


@bp.route("/milestones/<int:milestone_id>/unship", methods=["POST"])
def unship_milestone_route(milestone_id):
    db.unship_milestone(milestone_id)
    return redirect(url_for("main.history"))


def _csv_safe(value):
    """Excelが数式として解釈しないよう先頭の記号を無害化する(視聴者名は信用できない入力のため)。"""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t"):
        return "'" + s
    return s


@bp.route("/history.csv")
def history_csv():
    shipped = db.list_shipped_milestones()
    buf = io.StringIO()
    buf.write("﻿")  # ExcelでUTF-8として開けるようBOMを付与
    writer = csv.writer(buf)
    writer.writerow(["login", "表示名", "種別", "月数", "到達日時", "特典日時", "メモ"])
    for row in shipped:
        kind_label = "通算" if row["kind"] == "cumulative" else "連続"
        writer.writerow(
            [
                _csv_safe(row["login"]),
                _csv_safe(row["display_name"] or row["login"]),
                kind_label,
                row["threshold"],
                row["reached_at"],
                row["shipped_at"],
                _csv_safe(row["memo"] or ""),
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=shipping_history.csv"},
    )


@bp.route("/subscribers")
def subscribers():
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    page_size = config.RANKING_TOP_N
    offset = (page - 1) * page_size

    total = db.count_all_sub_states()
    total_pages = max((total + page_size - 1) // page_size, 1)
    rows = db.list_all_sub_states(limit=page_size, offset=offset)

    return render_template(
        "subscribers.html",
        rows=rows,
        tier_labels=config.TIER_LABELS,
        page=page,
        total_pages=total_pages,
        page_window=_page_window(page, total_pages),
        active="subscribers",
    )


@bp.route("/subscribers/manual", methods=["POST"])
def subscribers_manual():
    login = request.form.get("login", "").strip().lower()
    display_name = request.form.get("display_name", "").strip() or login
    cumulative_raw = request.form.get("cumulative_months", "").strip()
    streak_raw = request.form.get("streak_months", "").strip()
    tier_raw = request.form.get("tier", "").strip()

    if not login:
        return redirect(url_for("main.subscribers"))

    # 入力ミスによる巨大値で特典待ちが埋まらないよう上限1200ヶ月(100年)に制限
    cumulative = min(int(cumulative_raw), 1200) if cumulative_raw.isdigit() else None
    streak = min(int(streak_raw), 1200) if streak_raw.isdigit() else None

    # 空欄の項目は「変更しない」扱い(既存の月数を消さない)
    existing = db.get_sub_state(db.current_channel(), login)
    if existing:
        if cumulative is None:
            cumulative = existing["cumulative_months"]
        if streak is None:
            streak = existing["streak_months"]

    if tier_raw in config.ALL_TIERS:
        tier = tier_raw
    else:
        tier = existing["tier"] if existing else None

    milestone.handle_manual_update(login, display_name, cumulative, streak, tier=tier)
    return redirect(url_for("main.subscribers"))


@bp.route("/subscribers.csv")
def subscribers_csv():
    rows = db.list_all_sub_states()
    buf = io.StringIO()
    buf.write("﻿")  # ExcelでUTF-8として開けるようBOMを付与
    writer = csv.writer(buf)
    writer.writerow(["login", "表示名", "通算月数", "連続月数", "ティア", "更新日時"])
    for r in rows:
        writer.writerow(
            [
                _csv_safe(r["login"]),
                _csv_safe(r["display_name"] or r["login"]),
                r["cumulative_months"] if r["cumulative_months"] is not None else "",
                r["streak_months"] if r["streak_months"] is not None else "",
                config.TIER_LABELS.get(r["tier"], r["tier"] or ""),
                r["updated_at"] or "",
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers.csv"},
    )


def _redirect_back(fallback_endpoint):
    """安全な自サイト内リダイレクト。beforeで検証済みのローカルホストからの参照のみ信用する。"""
    ref = request.referrer
    if ref and ref.startswith(request.host_url):
        return redirect(ref)
    return redirect(url_for(fallback_endpoint))


@bp.route("/viewers/<login>/delete", methods=["POST"])
def delete_viewer(login):
    db.delete_viewer(db.current_channel(), login.lower())
    return _redirect_back("main.subscribers")


@bp.route("/viewers/<login>/rename", methods=["POST"])
def rename_viewer(login):
    new_name = request.form.get("display_name", "").strip()
    if new_name:
        db.rename_viewer(db.current_channel(), login.lower(), new_name)
    return _redirect_back("main.subscribers")


@bp.route("/forecast")
def forecast():
    upcoming = db.forecast_upcoming()
    return render_template("forecast.html", upcoming=upcoming, active="forecast")


def _page_window(current, total, radius=2):
    """ページ番号ボタン用の一覧。多すぎる場合はNone(...)で省略する。
    例: 1 2 3 ... 10 11 12 ... 55 56"""
    pages = {1, total}
    for p in range(current - radius, current + radius + 1):
        if 1 <= p <= total:
            pages.add(p)
    ordered = sorted(pages)
    result = []
    prev = None
    for p in ordered:
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result


@bp.route("/ranking")
def ranking_page():
    tab = request.args.get("tab", "message")
    period = request.args.get("period", "all")
    if period not in ("all", "month", "week"):
        period = "all"

    page = max(request.args.get("page", 1, type=int) or 1, 1)
    page_size = config.RANKING_TOP_N
    offset = (page - 1) * page_size

    keywords = db.list_keyword_defs()
    keyword_id = request.args.get("keyword_id", type=int)

    if tab == "keyword":
        rows = db.keyword_ranking(period=period, keyword_id=keyword_id, offset=offset)
        total = db.keyword_ranking_count(period=period, keyword_id=keyword_id)
    elif tab == "bits":
        rows = db.bits_ranking(period=period, offset=offset)
        total = db.bits_ranking_count(period=period)
    elif tab == "gift":
        rows = db.gift_ranking(period=period, offset=offset)
        total = db.gift_ranking_count(period=period)
    else:
        tab = "message"
        rows = db.message_ranking(period=period, offset=offset)
        total = db.message_ranking_count(period=period)

    total_pages = max((total + page_size - 1) // page_size, 1)

    return render_template(
        "ranking.html",
        tab=tab,
        period=period,
        rows=rows,
        keywords=keywords,
        keyword_id=keyword_id,
        page=page,
        total_pages=total_pages,
        page_window=_page_window(page, total_pages),
        rank_offset=offset,
        active="ranking",
    )


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        channel_name = request.form.get("channel_name", "").strip()
        cumulative_raw = request.form.get("cumulative_thresholds", "").strip()
        streak_raw = request.form.get("streak_thresholds", "").strip()
        cumulative_interval_raw = request.form.get("cumulative_interval", "").strip()
        streak_interval_raw = request.form.get("streak_interval", "").strip()
        notify_enabled = "1" if request.form.get("notify_enabled") == "on" else "0"
        eligible_tiers = request.form.getlist("eligible_tiers")
        autostart_enabled = request.form.get("autostart_enabled") == "on"
        kind_cumulative = request.form.get("kind_cumulative") == "on"
        kind_streak = request.form.get("kind_streak") == "on"
        update_check_enabled = request.form.get("update_check_enabled") == "on"

        if channel_name:
            # URL貼り付けや大文字も受け付けてログイン名に正規化する(無効な入力は保存しない)
            new_channel = db.normalize_channel(channel_name)
            if new_channel:
                db.set_setting("channel_name", new_channel)
                client = state.get_irc_client()
                if client:
                    client.set_channel(new_channel)

        def parse_thresholds(raw):
            values = []
            for part in raw.split(","):
                part = part.strip()
                if part.isdigit():
                    values.append(int(part))
            return values

        cumulative = parse_thresholds(cumulative_raw)
        streak = parse_thresholds(streak_raw)
        if cumulative:
            db.set_thresholds("cumulative", cumulative)
        if streak:
            db.set_thresholds("streak", streak)

        db.set_interval("cumulative", int(cumulative_interval_raw) if cumulative_interval_raw.isdigit() else None)
        db.set_interval("streak", int(streak_interval_raw) if streak_interval_raw.isdigit() else None)

        db.set_setting("notify_enabled", notify_enabled)
        db.set_eligible_tiers(eligible_tiers)
        db.set_kind_enabled("cumulative", kind_cumulative)
        db.set_kind_enabled("streak", kind_streak)
        db.set_update_check_enabled(update_check_enabled)
        db.set_count_mode(request.form.get("count_mode", "absolute"))

        if autostart_enabled:
            autostart.enable()
        else:
            autostart.disable()

        return redirect(url_for("main.settings", saved=1))

    ctx = {
        "channel_name": db.get_setting("channel_name", ""),
        "cumulative_thresholds": ",".join(str(t) for t in db.get_thresholds("cumulative")),
        "streak_thresholds": ",".join(str(t) for t in db.get_thresholds("streak")),
        "cumulative_interval": db.get_interval("cumulative") or "",
        "streak_interval": db.get_interval("streak") or "",
        "notify_enabled": db.is_notify_enabled(),
        "all_tiers": config.ALL_TIERS,
        "tier_labels": config.TIER_LABELS,
        "eligible_tiers": db.get_eligible_tiers(),
        "autostart_available": autostart.is_available(),
        "count_mode": db.get_count_mode(),
        "update_check_enabled": db.is_update_check_enabled(),
        "update_check_configured": bool(config.UPDATE_CHECK_URL),
        "autostart_enabled": autostart.is_enabled(),
        "kind_cumulative": db.is_kind_enabled("cumulative"),
        "kind_streak": db.is_kind_enabled("streak"),
        "keywords": [
            {"id": k["id"], "label": k["label"], "patterns": ", ".join(json.loads(k["patterns"]))}
            for k in db.list_keyword_defs()
        ],
        "saved": request.args.get("saved") == "1",
        "active": "settings",
    }
    return render_template("settings.html", **ctx)


@bp.route("/settings/keywords/add", methods=["POST"])
def add_keyword():
    label = request.form.get("label", "").strip()
    patterns_raw = request.form.get("patterns", "").strip()
    patterns = [p.strip() for p in patterns_raw.split(",") if p.strip()]

    if label and patterns:
        db.add_keyword_def(label, patterns)
        ranking.reload_keyword_cache()

    return redirect(url_for("main.settings"))


@bp.route("/settings/keywords/<int:keyword_id>/delete", methods=["POST"])
def delete_keyword(keyword_id):
    db.delete_keyword_def(keyword_id)
    ranking.reload_keyword_cache()
    return redirect(url_for("main.settings"))


@bp.route("/autostart/enable", methods=["POST"])
def autostart_enable():
    """チュートリアルから自動起動をONにするためのエンドポイント。"""
    autostart.enable()
    return ("", 204)


# ---------- OBSブラウザソース用オーバーレイ ----------

@bp.route("/overlay/ranking")
def overlay_ranking():
    tab = request.args.get("tab", "message")
    if tab not in ("message", "keyword", "bits", "gift"):
        tab = "message"
    period = request.args.get("period", "week")
    if period not in ("all", "month", "week"):
        period = "week"
    return render_template("overlay_ranking.html", tab=tab, period=period)


@bp.route("/overlay/alert")
def overlay_alert():
    return render_template("overlay_alert.html")


@bp.route("/api/overlay/ranking")
def api_overlay_ranking():
    tab = request.args.get("tab", "message")
    period = request.args.get("period", "week")
    if period not in ("all", "month", "week"):
        period = "week"

    if tab == "keyword":
        rows = db.keyword_ranking(period=period, limit=10)
    elif tab == "bits":
        rows = db.bits_ranking(period=period, limit=10)
    elif tab == "gift":
        rows = db.gift_ranking(period=period, limit=10)
    else:
        tab = "message"
        rows = db.message_ranking(period=period, limit=10)

    labels = {"message": "コメント数", "keyword": "キーワード", "bits": "ビッツ", "gift": "ギフト"}
    return jsonify(
        {
            "title": labels[tab],
            "rows": [
                {"name": r["display_name"] or r["login"], "value": r["c"]}
                for r in rows
            ],
        }
    )


@bp.route("/api/overlay/latest_milestone")
def api_overlay_latest_milestone():
    row = db.latest_milestone()
    if row is None:
        return jsonify({"id": None})
    return jsonify(
        {
            "id": row["id"],
            "name": row["display_name"] or row["login"],
            "kind": "通算" if row["kind"] == "cumulative" else "連続",
            "threshold": row["threshold"],
        }
    )


def create_app():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app
