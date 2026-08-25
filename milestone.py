"""サブスクイベントの解釈と閾値判定。USERNOTICE解析結果を受け取ってDBに反映する。"""

import datetime

import db
import notifier
import ranking

SUB_EVENT_TYPES = {"sub", "resub", "subgift", "submysterygift", "giftpaidupgrade", "anongiftpaidupgrade"}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def handle_usernotice(parsed, notify=True):
    msg_id = parsed.tags.get("msg-id", "")
    if msg_id not in SUB_EVENT_TYPES:
        return

    # コラボ配信の統合チャットでは相方のチャンネルのサブスクも流れてくるため、
    # 自分のチャンネルの記録に混ざらないよう除外する
    if parsed.is_from_other_channel and db.is_shared_chat_ignored():
        return

    login = parsed.login
    channel = parsed.channel
    if not login or not channel:
        return
    display_name = parsed.tags.get("display-name", login)

    cumulative_raw = parsed.tags.get("msg-param-cumulative-months")
    cumulative = int(cumulative_raw) if cumulative_raw and cumulative_raw.isdigit() else None

    should_share_streak = parsed.tags.get("msg-param-should-share-streak") == "1"
    streak_raw = parsed.tags.get("msg-param-streak-months")
    streak = int(streak_raw) if should_share_streak and streak_raw and streak_raw.isdigit() else None

    tier = parsed.tags.get("msg-param-sub-plan")

    now = _now()

    db.upsert_viewer(channel, login, display_name)
    db.record_sub_event(channel, login, msg_id, cumulative, streak, tier, now)

    # 通算/連続月数が確定するのは本人の sub / resub イベントのみ
    if msg_id in ("sub", "resub") and cumulative is not None:
        db.upsert_sub_state(
            channel,
            login,
            display_name,
            cumulative_months=cumulative,
            streak_months=streak,
            tier=tier,
            source="chat",
            updated_at=now,
        )
        _check_with_mode(channel, login, cumulative, streak, now, tier, notify)

    # ギフトサブ: 贈り主をランキング用にカウントし、受け取り主もサブスクとして記録する
    # (submysterygiftは「◯個贈った」という予告で、直後に個別のsubgiftがその数だけ流れるため
    #  二重カウントを避けて subgift のみを数える)
    if msg_id == "subgift":
        db.record_gift(channel, login, 1, now)

        recipient = (parsed.tags.get("msg-param-recipient-user-name") or "").lower()
        if recipient:
            recipient_display = parsed.tags.get("msg-param-recipient-display-name", recipient)
            months_raw = parsed.tags.get("msg-param-months")
            months = int(months_raw) if months_raw and months_raw.isdigit() else None
            db.upsert_viewer(channel, recipient, recipient_display)
            if months is not None:
                db.upsert_sub_state(
                    channel,
                    recipient,
                    recipient_display,
                    cumulative_months=months,
                    streak_months=None,
                    tier=tier,
                    source="chat",
                    updated_at=now,
                )
                _check_with_mode(channel, recipient, months, None, now, tier, notify)


def _check_with_mode(channel, login, cumulative_months, streak_months, reached_at, tier, notify):
    """数え方モード(通算そのもの/導入時からの増加分)を反映して閾値判定する。"""
    row = db.get_sub_state(channel, login)
    base = row["base_months"] if row else None
    effective = db.effective_cumulative(cumulative_months, base)
    check_milestones(channel, login, effective, streak_months, reached_at, tier=tier, notify=notify)


def check_milestones(channel, login, cumulative_months, streak_months, reached_at, tier=None, notify=True):
    # 特典対象ティアの設定に含まれない人は、月数は記録するが特典待ちには載せない
    if not db.is_tier_eligible(tier):
        return

    should_notify = notify and db.is_notify_enabled()

    for kind, months in (("cumulative", cumulative_months), ("streak", streak_months)):
        if months is None or not db.is_kind_enabled(kind):
            continue
        newly_added = []
        for threshold in db.thresholds_up_to(kind, months):
            if db.try_add_milestone(channel, login, kind, threshold, reached_at):
                newly_added.append(threshold)
        # 導入時に大量の過去分が一気に閾値到達しても、通知は最高到達の1件だけに絞る
        if newly_added and should_notify:
            notifier.notify_milestone(login, kind, max(newly_added))


def handle_manual_update(login, display_name, cumulative_months, streak_months, tier=None):
    """配信者が手入力した月数を反映する。現在設定されているチャンネルに対して適用し、通知は出さない。"""
    now = _now()
    login = login.lower().strip()
    channel = db.current_channel()
    db.upsert_viewer(channel, login, display_name)
    db.upsert_sub_state(
        channel,
        login,
        display_name,
        cumulative_months=cumulative_months,
        streak_months=streak_months,
        tier=tier,
        source="manual",
        updated_at=now,
    )
    _check_with_mode(channel, login, cumulative_months, streak_months, now, tier, False)


def recheck_all():
    """記録済みの全員を、現在の設定でもう一度判定する。

    節目の判定は「新しいサブスクのイベントが届いた瞬間」にしか行われないため、
    設定(閾値・対象ティア・数え方など)をあとから変えても、すでに記録済みの人には
    反映されない。このずれを埋めるために使う。
    足りない分を追加するだけで、すでにある特典を消したり戻したりはしない。"""
    channel = db.current_channel()
    rows = db.list_all_sub_states()
    before = db.count_all_milestones(channel)

    for row in rows:
        _check_with_mode(
            channel,
            row["login"],
            row["cumulative_months"],
            row["streak_months"],
            row["updated_at"] or _now(),
            row["tier"],
            False,  # まとめて判定するので通知は出さない
        )

    added = db.count_all_milestones(channel) - before
    return {"people": len(rows), "added": added}


def handle_privmsg(parsed):
    login = parsed.login
    channel = parsed.channel
    if not login or not channel:
        return

    # 統合チャット経由の他チャンネルのコメントは数えない(上記と同じ理由)
    if parsed.is_from_other_channel and db.is_shared_chat_ignored():
        return
    display_name = parsed.tags.get("display-name", login)
    db.upsert_viewer(channel, login, display_name, seen_message=True)
    ranking.process_privmsg(parsed)
