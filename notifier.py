"""Windowsデスクトップ通知。winotifyが使えない環境でも落ちないようにする。"""

APP_ID = "配信サポートツール"

try:
    from winotify import Notification
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def notify_milestone(login, kind, threshold):
    kind_label = "通算" if kind == "cumulative" else "連続"
    title = f"🎉 {kind_label}{threshold}ヶ月到達"
    msg = f"{login} さんが{kind_label}{threshold}ヶ月に到達しました"
    notify_info(title, msg)


def notify_info(title, msg):
    if not _AVAILABLE:
        print(f"[通知] {title}: {msg}")
        return

    try:
        toast = Notification(app_id=APP_ID, title=title, msg=msg)
        toast.show()
    except Exception as e:
        print(f"[通知エラー] {e}: {title} / {msg}")
