"""Windows画面右下(通知領域)への常駐アイコン。

起動中かひと目で分かるようにし、右クリックから管理画面を開いたり終了したりできるようにする。
pystrayが使えない環境では何もしない(ツール本体の動作には影響しない)。
"""

import os
import threading

import browser_window
import config

try:
    import pystray
    from PIL import Image

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

ADMIN_URL = f"http://{config.FLASK_HOST}:{config.FLASK_PORT}"

_icon = None


def is_available():
    return _AVAILABLE


def _load_image():
    """トレイアイコン用の画像。無ければ紫の四角で代用する。"""
    ico_path = os.path.join(config.RESOURCE_DIR, "web", "static", "favicon.ico")
    if os.path.exists(ico_path):
        return Image.open(ico_path)
    return Image.new("RGB", (64, 64), (145, 70, 255))


def _open_admin(icon=None, item=None):
    browser_window.open_or_focus(ADMIN_URL)


def _open_overlay_help(icon=None, item=None):
    browser_window.open_or_focus(f"{ADMIN_URL}/settings")


def _quit(icon, item=None):
    # 画面に残っている通知と管理画面のウィンドウを片付けてから終了する
    try:
        import notifier
        notifier.clear_all()
    except Exception:
        pass
    try:
        browser_window.close_all()
    except Exception:
        pass
    icon.visible = False
    icon.stop()
    # Flaskは別スレッドで動いているため、プロセスごと終了させる
    os._exit(0)


def _status_text(item=None):
    import state

    status = state.get_status()
    channel = ""
    try:
        import db

        channel = db.current_channel()
    except Exception:
        pass

    if status == "connected":
        label = f"🟢 監視中: #{channel}" if channel else "🟢 接続中"
    elif status == "connecting":
        label = "🟡 再接続中..."
    else:
        label = "🔴 未接続(チャンネル未設定)" if not channel else "🔴 切断中"
    return label


def _tooltip():
    return f"特典台帳 - {_status_text()}"


def _keep_tooltip_updated():
    """接続状態が変わったらツールチップに反映する(マウスを乗せるだけで分かるように)。"""
    import time

    last = None
    while _icon is not None:
        try:
            current = _tooltip()
            if current != last:
                _icon.title = current
                last = current
        except Exception:
            pass
        time.sleep(5)


def start():
    """トレイアイコンを別スレッドで常駐させる。"""
    global _icon
    if not _AVAILABLE:
        return None

    menu = pystray.Menu(
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("管理画面を開く", _open_admin, default=True),
        pystray.MenuItem("設定を開く", _open_overlay_help),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("終了", _quit),
    )

    # マウスを乗せたときにも状態が分かるようにする
    _icon = pystray.Icon("TokutenDaicho", _load_image(), _tooltip(), menu)
    threading.Thread(target=_keep_tooltip_updated, daemon=True).start()

    thread = threading.Thread(target=_icon.run, daemon=True)
    thread.start()
    return _icon


def notify(title, message):
    """トレイからの簡易通知(使える場合のみ)。"""
    if _icon is not None:
        try:
            _icon.notify(message, title)
        except Exception:
            pass
