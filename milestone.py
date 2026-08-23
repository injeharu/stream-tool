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

    login = parsed.login
    if not login:
        return
    display_name = parsed.tags.get("display-name", login)

    cumulative_raw = parsed.tags.get("msg-param-cumulative-months")
    cumulative = int(cumulative_raw) if cumulative_raw and cumulative_raw.isdigit() else None

    should_share_streak = parsed.tags.get("msg-param-should-share-streak") == "1"
    streak_raw = parsed.tags.get("msg-param-streak-months")
    streak = int(streak_raw) if should_share_streak and streak_raw and streak_raw.isdigit() else None

    tier = parsed.tags.get("msg-param-sub-plan")

    now = _now()

    db.upsert_viewer(login, display_name)
    db.record_sub_event(login, msg_id, cumulative, streak, tier, now)

    # 通算/連続月数が確定するのは本人の sub / resub イベントのみ
    # (subgiftは贈り主のイベントであり、受け取り側の月数はこのタグに入らない)
    if msg_id in ("sub", "resub") and cumulative is not None:
        db.upsert_sub_state(
            login,
            display_name,
            cumulative_months=cumulative,
            streak_months=streak,
            tier=tier,
            source="chat",
            updated_at=now,
        )
        check_milestones(login, cumulative, streak, now, tier=tier, notify=notify)


def check_milestones(login, cumulative_months, streak_months, reached_at, tier=None, notify=True):
    # 発送対象ティアの設定に含まれない人は、月数は記録するが発送待ちには載せない
    if not db.is_tier_eligible(tier):
        return

    should_notify = notify and db.is_notify_enabled()

    for kind, months in (("cumulative", cumulative_months), ("streak", streak_months)):
        if months is None or not db.is_kind_enabled(kind):
            continue
        newly_added = []
        for threshold in db.thresholds_up_to(kind, months):
            if db.try_add_milestone(login, kind, threshold, reached_at):
                newly_added.append(threshold)
        # 導入時に大量の過去分が一気に閾値到達しても、通知は最高到達の1件だけに絞る
        if newly_added and should_notify:
            notifier.notify_milestone(login, kind, max(newly_added))


def handle_manual_update(login, display_name, cumulative_months, streak_months, tier=None):
    """配信者が手入力した月数を反映する。通知は出さない(自分の入力に反応させないため)。"""
    now = _now()
    login = login.lower().strip()
    db.upsert_viewer(login, display_name)
    db.upsert_sub_state(
        login,
        display_name,
        cumulative_months=cumulative_months,
        streak_months=streak_months,
        tier=tier,
        source="manual",
        updated_at=now,
    )
    check_milestones(login, cumulative_months, streak_months, now, tier=tier, notify=False)


def handle_privmsg(parsed):
    login = parsed.login
    if not login:
        return
    display_name = parsed.tags.get("display-name", login)
    db.upsert_viewer(login, display_name, seen_message=True)
    ranking.process_privmsg(parsed)
