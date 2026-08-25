"""管理画面のFlaskルート定義。"""

import csv
import io
import json
import os

from urllib.parse import urlparse

from flask import (
    Flask, Blueprint, render_template, request, redirect, url_for,
    Response, abort, jsonify, send_file,
)

import autostart
import config
import db
import state
import milestone
import ranking
import twitch_api
import updater

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
        "is_frozen": config.IS_FROZEN,
        # 連携済みのときだけ、各画面に取得元の案内を出すために使う。
        # 「今見ているチャンネルの名簿を取り込み済みか」までを条件にして、
        # 別チャンネルを見ているときに的外れな案内が出ないようにする
        "twitch_linked": twitch_api.is_linked() and db.count_twitch_subscribers() > 0,
    }


@bp.route("/api/alive")
def api_alive():
    """ページ側の生存確認用。アプリ終了を画面が検知するために使う。"""
    return jsonify({"ok": True, "version": config.APP_VERSION})


@bp.route("/update/start", methods=["POST"])
def update_start():
    """ワンクリック更新の開始(exe版のみ)。"""
    if not config.IS_FROZEN:
        return jsonify({"ok": False, "message": "ソース実行版は git pull で更新してください"}), 400
    updater.start_one_click_update()
    return jsonify({"ok": True})


@bp.route("/api/update/progress")
def api_update_progress():
    return jsonify(state.get_update_progress())


@bp.route("/update/check", methods=["POST"])
def update_check_now():
    """設定画面の「今すぐ確認」ボタン。自動確認OFFでも手動なら実行する。"""
    ok = updater.check_once(force=True)
    info = state.get_available_update()
    return jsonify(
        {
            "ok": ok,
            "current": config.APP_VERSION,
            "update": info is not None,
            "version": info["version"] if info else None,
        }
    )


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
        adjust_error=request.args.get("adjust_error"),
        active="ranking",
    )


@bp.route("/ranking/adjust", methods=["POST"])
def ranking_adjust():
    """ギフト・ビッツの手入力調整。自動記録は壊さず、調整分だけを設定する。"""
    kind = request.form.get("kind", "")
    if kind not in ("gift", "bits"):
        return redirect(url_for("main.ranking_page"))

    login = db.normalize_channel(request.form.get("login", "")) or ""
    amount_raw = request.form.get("amount", "").strip()
    display_name = request.form.get("display_name", "").strip()

    if not login:
        return redirect(url_for("main.ranking_page", tab=kind, adjust_error="login"))

    try:
        amount = int(amount_raw)
    except ValueError:
        return redirect(url_for("main.ranking_page", tab=kind, adjust_error="amount"))

    # 入力ミスによる極端な値を防ぐ
    amount = max(0, min(amount, 10_000_000))

    channel = db.current_channel()
    # 未知の視聴者でも一覧に名前が出るよう、最低限の情報を登録しておく
    db.upsert_viewer(channel, login, display_name or login)
    db.set_adjustment(channel, login, kind, amount)
    return redirect(url_for("main.ranking_page", tab=kind, saved=1))


# ---------- Twitch連携(任意機能) ----------

@bp.route("/twitch/auth/start", methods=["POST"])
def twitch_auth_start():
    """連携を開始し、利用者に見せるコードを返す。"""
    try:
        info = twitch_api.start_device_auth()
        return jsonify({"ok": True, **info})
    except twitch_api.TwitchApiError as e:
        return jsonify({"ok": False, "message": str(e)})
    except Exception:
        # 想定外の失敗でもエラー画面ではなく通常のメッセージで返す(保険)
        return jsonify({"ok": False, "message": "連携を開始できませんでした。時間をおいてお試しください"})


@bp.route("/api/twitch/status")
def api_twitch_status():
    """連携の状態(画面が待機中かどうかを知るために使う)。"""
    return jsonify(
        {
            "linked": twitch_api.is_linked(),
            "authenticating": twitch_api.is_authenticating(),
            "login": db.get_setting("twitch_user_login", ""),
        }
    )


@bp.route("/twitch/unlink", methods=["POST"])
def twitch_unlink():
    twitch_api.unlink()
    return redirect(url_for("main.settings", saved=1))


@bp.route("/twitch/sync", methods=["POST"])
def twitch_sync():
    """連携で取れる情報をまとめて取り込む。"""
    try:
        result = twitch_api.sync_all()
        return jsonify({"ok": True, **result})
    except twitch_api.TwitchApiError as e:
        return jsonify({"ok": False, "message": str(e)})
    except Exception:
        return jsonify({"ok": False, "message": "取り込みに失敗しました。時間をおいてお試しください"})


@bp.route("/twitch")
def twitch_page():
    """連携で取得した情報の一覧画面。"""
    if not twitch_api.is_linked():
        return render_template("twitch.html", linked=False, active="twitch")

    page = max(request.args.get("page", 1, type=int) or 1, 1)
    tab = request.args.get("tab", "subscribers")
    if tab not in ("subscribers", "followers", "bits"):
        tab = "subscribers"

    page_size = config.RANKING_TOP_N
    offset = (page - 1) * page_size

    if tab == "followers":
        rows = db.list_twitch_followers(limit=page_size, offset=offset)
        total = db.count_twitch_followers()
    elif tab == "bits":
        rows = db.list_twitch_bits(limit=page_size, offset=offset)
        total = db.count_twitch_bits()
    else:
        rows = db.list_twitch_subscribers(limit=page_size, offset=offset)
        total = db.count_twitch_subscribers()

    total_pages = max((total + page_size - 1) // page_size, 1)

    return render_template(
        "twitch.html",
        linked=True,
        tab=tab,
        rows=rows,
        total=total,
        stats=db.twitch_subscriber_stats(),
        follower_count=db.count_twitch_followers(),
        bits_count=db.count_twitch_bits(),
        goals=db.get_twitch_goals(),
        tier_labels=config.TIER_LABELS,
        linked_login=db.get_setting("twitch_user_login", ""),
        synced_at=db.get_setting("twitch_subs_synced_at", ""),
        page=page,
        total_pages=total_pages,
        page_window=_page_window(page, total_pages),
        rank_offset=offset,
        active="twitch",
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
        db.set_notify_persistent(request.form.get("notify_persistent") == "on")
        db.set_shared_chat_ignored(request.form.get("ignore_shared_chat") == "on")
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
        "notify_persistent": db.is_notify_persistent(),
        "ignore_shared_chat": db.is_shared_chat_ignored(),
        "custom_sound": db.get_custom_sound(),
        "sound_volume": db.get_sound_volume(),
        "sound_error": request.args.get("sound_error"),
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
    # テスト発火が押されていればそちらを優先(OBSでの見え方確認用)
    test = state.get_test_alert()
    if test is not None:
        return jsonify(
            {
                "id": f"test-{test['seq']}",
                "name": test["name"],
                "kind": test["kind"],
                "threshold": test["threshold"],
                "test": True,
            }
        )

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


@bp.route("/settings/sound", methods=["POST"])
def upload_sound():
    """オーバーレイのカスタム効果音を登録する。"""
    file = request.files.get("sound")
    if not file or not file.filename:
        return redirect(url_for("main.settings"))

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in config.ALLOWED_SOUND_EXTENSIONS:
        return redirect(url_for("main.settings", sound_error="type"))

    os.makedirs(config.SOUND_DIR, exist_ok=True)
    # 保存名は固定にして、古い音源が残り続けないようにする
    for old in os.listdir(config.SOUND_DIR):
        if old.startswith("custom"):
            try:
                os.remove(os.path.join(config.SOUND_DIR, old))
            except OSError:
                pass

    filename = f"custom{ext}"
    path = os.path.join(config.SOUND_DIR, filename)
    file.save(path)

    if os.path.getsize(path) > config.MAX_SOUND_BYTES:
        os.remove(path)
        return redirect(url_for("main.settings", sound_error="size"))

    db.set_custom_sound(filename)
    return redirect(url_for("main.settings", saved=1))


@bp.route("/settings/sound/volume", methods=["POST"])
def set_sound_volume_route():
    """効果音の音量だけを保存する(OBS連携セクション内で完結させるため)。"""
    db.set_sound_volume(request.form.get("sound_volume", 70))
    return jsonify({"ok": True, "volume": db.get_sound_volume()})


@bp.route("/settings/sound/delete", methods=["POST"])
def delete_sound():
    """カスタム効果音を削除して内蔵音に戻す。"""
    current = db.get_custom_sound()
    if current:
        path = os.path.join(config.SOUND_DIR, current)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    db.set_custom_sound("")
    return redirect(url_for("main.settings", saved=1))


@bp.route("/sound/custom")
def serve_custom_sound():
    """登録された効果音をオーバーレイへ配信する。"""
    filename = db.get_custom_sound()
    if not filename:
        abort(404)
    # 設定に保存された名前のみを使い、任意のパスを読み出せないようにする
    safe_name = os.path.basename(filename)
    path = os.path.join(config.SOUND_DIR, safe_name)
    if not os.path.exists(path):
        abort(404)
    return send_file(path)


@bp.route("/api/overlay/sound")
def api_overlay_sound():
    """オーバーレイ側が効果音の設定を取得するためのAPI。"""
    return jsonify(
        {
            "custom": bool(db.get_custom_sound()),
            "url": "/sound/custom" if db.get_custom_sound() else None,
            "volume": db.get_sound_volume() / 100.0,
        }
    )


@bp.route("/overlay/test", methods=["POST"])
def overlay_test():
    """設定画面から祝福オーバーレイをテスト表示する。"""
    name = request.form.get("name", "").strip() or "テスト視聴者"
    threshold = request.form.get("threshold", type=int) or 12
    kind = "連続" if request.form.get("kind") == "streak" else "通算"
    state.fire_test_alert(name, kind, threshold)
    return redirect(url_for("main.settings"))


def create_app():
    # exe化後はモジュールの__file__基準のパス解決が信用できないため、
    # テンプレート/静的ファイルの場所を明示的に指定する
    template_folder = os.path.join(config.RESOURCE_DIR, "web", "templates")
    static_folder = os.path.join(config.RESOURCE_DIR, "web", "static")
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
    app.register_blueprint(bp)
    return app
