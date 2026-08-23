"""管理画面のFlaskルート定義。"""

import csv
import io
import json

from flask import Flask, Blueprint, render_template, request, redirect, url_for, Response

import autostart
import config
import db
import state
import milestone
import ranking

bp = Blueprint("main", __name__)


@bp.context_processor
def inject_status():
    return {
        "irc_status": state.get_status(),
        "available_update": state.get_available_update(),
        "app_version": config.APP_VERSION,
        "tutorial_seen": db.is_tutorial_seen(),
    }


@bp.route("/tutorial/seen", methods=["POST"])
def tutorial_seen():
    db.mark_tutorial_seen()
    return ("", 204)


@bp.route("/")
def index():
    pending = db.list_pending_milestones()
    tiles = {
        "pending_count": db.count_pending_milestones(),
        "reached_this_month": db.count_milestones_reached_this_month(),
        "known_subscribers": db.count_known_subscribers(),
    }
    return render_template("index.html", pending=pending, tiles=tiles, active="index")


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
    return render_template("history.html", shipped=shipped, active="history")


@bp.route("/history.csv")
def history_csv():
    shipped = db.list_shipped_milestones()
    buf = io.StringIO()
    buf.write("﻿")  # ExcelでUTF-8として開けるようBOMを付与
    writer = csv.writer(buf)
    writer.writerow(["login", "表示名", "種別", "月数", "到達日時", "発送日時", "メモ"])
    for row in shipped:
        kind_label = "通算" if row["kind"] == "cumulative" else "連続"
        writer.writerow(
            [
                row["login"],
                row["display_name"] or row["login"],
                kind_label,
                row["threshold"],
                row["reached_at"],
                row["shipped_at"],
                row["memo"] or "",
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=shipping_history.csv"},
    )


@bp.route("/subscribers")
def subscribers():
    rows = db.list_all_sub_states()
    return render_template(
        "subscribers.html", rows=rows, tier_labels=config.TIER_LABELS, active="subscribers"
    )


@bp.route("/subscribers/manual", methods=["POST"])
def subscribers_manual():
    login = request.form.get("login", "").strip().lower()
    display_name = request.form.get("display_name", "").strip() or login
    cumulative_raw = request.form.get("cumulative_months", "").strip()
    streak_raw = request.form.get("streak_months", "").strip()

    if not login:
        return redirect(url_for("main.subscribers"))

    # 入力ミスによる巨大値で発送待ちが埋まらないよう上限1200ヶ月(100年)に制限
    cumulative = min(int(cumulative_raw), 1200) if cumulative_raw.isdigit() else None
    streak = min(int(streak_raw), 1200) if streak_raw.isdigit() else None

    # 空欄の項目は「変更しない」扱い(既存の月数を消さない)
    existing = db.get_sub_state(login)
    if existing:
        if cumulative is None:
            cumulative = existing["cumulative_months"]
        if streak is None:
            streak = existing["streak_months"]
    tier = existing["tier"] if existing else None

    milestone.handle_manual_update(login, display_name, cumulative, streak, tier=tier)
    return redirect(url_for("main.subscribers"))


@bp.route("/forecast")
def forecast():
    upcoming = db.forecast_upcoming()
    return render_template("forecast.html", upcoming=upcoming, active="forecast")


@bp.route("/ranking")
def ranking_page():
    tab = request.args.get("tab", "message")
    period = request.args.get("period", "all")
    if period not in ("all", "month", "week"):
        period = "all"

    keywords = db.list_keyword_defs()
    keyword_id = request.args.get("keyword_id", type=int)

    if tab == "keyword":
        rows = db.keyword_ranking(period=period, keyword_id=keyword_id)
    elif tab == "bits":
        rows = db.bits_ranking(period=period)
    else:
        tab = "message"
        rows = db.message_ranking(period=period)

    return render_template(
        "ranking.html",
        tab=tab,
        period=period,
        rows=rows,
        keywords=keywords,
        keyword_id=keyword_id,
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
            db.set_setting("channel_name", channel_name.lower())

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


def create_app():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app
