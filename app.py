"""起動入口。IRC監視スレッドとFlask管理画面を立ち上げる。"""

import datetime
import glob
import os
import shutil
import socket
import sys
import threading
import webbrowser

import autostart
import browser_window
import config
import db
import state
import irc_parser
import milestone
import notifier
import ranking
import tray
import updater
from irc_client import AnonIrcClient
from web.routes import create_app


def on_line(raw):
    parsed = irc_parser.parse_line(raw)
    if not parsed:
        return
    if parsed.command == "USERNOTICE":
        milestone.handle_usernotice(parsed)
    elif parsed.command == "PRIVMSG":
        milestone.handle_privmsg(parsed)


BACKUP_KEEP = 3


def already_running():
    """同じPCでこのツールが既に動いているか(ポートが使用中か)を確認する。"""
    try:
        with socket.create_connection((config.FLASK_HOST, config.FLASK_PORT), timeout=1):
            return True
    except OSError:
        return False


def backup_db():
    """起動時に台帳を日次バックアップする(PC故障・誤操作からの保険。直近3世代のみ保持)。"""
    if not os.path.exists(config.DB_PATH):
        return
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y%m%d")
    dest = os.path.join(config.BACKUP_DIR, f"data-{today}.db")
    if not os.path.exists(dest):
        shutil.copyfile(config.DB_PATH, dest)
        print(f"[バックアップ] {dest}")
    old = sorted(glob.glob(os.path.join(config.BACKUP_DIR, "data-*.db")))
    for path in old[:-BACKUP_KEEP]:
        os.remove(path)


def main():
    url = f"http://{config.FLASK_HOST}:{config.FLASK_PORT}"

    # 通知クリック(tokutendaicho://)経由の呼び出しかどうか
    via_protocol = any(arg.startswith("tokutendaicho:") for arg in sys.argv[1:])

    # 二重起動ガード(スタートアップ自動起動+手動起動が重なった場合など)
    if already_running():
        if via_protocol:
            # 通知クリック: 静かに管理画面を前面化するだけ(追加の通知は出さない)
            browser_window.open_or_focus(url)
        else:
            print("すでに起動しています。管理画面を開きます。")
            notifier.notify_info("すでに起動しています", "特典台帳は起動済みです。管理画面を開きます。")
            browser_window.open_or_focus(url)
        return

    # 前回終了時・更新時に画面へ残った通知を掃除する
    notifier.clear_all()

    backup_db()
    db.init_db()
    ranking.reload_keyword_cache()
    updater.start_background_check()

    # 通知クリック(tokutendaicho://)でこのアプリが呼ばれるように登録する
    autostart.register_url_protocol()

    channel = db.get_setting("channel_name")
    # pythonw(スタートアップ起動)では sys.stdin が None になるため必ず存在確認する。
    # 通知クリック起動(via_protocol)では入力を求めない(待ちで固まらないように)
    if not channel and not via_protocol and sys.stdin is not None and sys.stdin.isatty():
        # 入力が読めない環境(ダブルクリック起動など)でも落ちないよう必ず握りつぶす
        try:
            raw = input("監視するTwitchチャンネル名(またはチャンネルURL)を入力してください: ")
        except (EOFError, OSError):
            raw = ""
        channel = db.normalize_channel(raw)
        if channel:
            db.set_setting("channel_name", channel)
        else:
            print("チャンネル名は設定画面からでも登録できます。")

    # チャンネル未設定でもスレッドは起動しておき、設定画面での入力を待機する
    # (保存すると即座に監視を始められるようにするため)
    client = AnonIrcClient(channel or "", on_line, status_callback=state.set_status)
    state.set_irc_client(client)
    irc_thread = threading.Thread(target=client.run_forever, daemon=True)
    irc_thread.start()

    if channel:
        print(f"チャンネル「{channel}」を監視しています。")
        notifier.notify_info("特典台帳を起動しました", f"「{channel}」のチャット監視を開始しました。")
    else:
        print("チャンネル名が未設定です。設定画面から登録してください。")
        notifier.notify_info(
            "特典台帳を起動しました",
            "チャンネル名が未設定です。設定画面から登録してください。",
        )

    print(f"管理画面: {url}")

    # 画面右下(通知領域)に常駐させ、起動中であることを分かるようにする
    tray.start()

    if config.IS_FROZEN:
        # exe版はコンソールが出ないため、起動時に自動で管理画面ウィンドウを開く
        threading.Timer(1.0, lambda: browser_window.open_or_focus(url)).start()
    app = create_app()
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, use_reloader=False)


if __name__ == "__main__":
    main()
