"""起動入口。IRC監視スレッドとFlask管理画面を立ち上げる。"""

import sys
import threading

import config
import db
import state
import irc_parser
import milestone
import notifier
import ranking
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


def main():
    db.init_db()
    ranking.reload_keyword_cache()
    updater.start_background_check()

    channel = db.get_setting("channel_name")
    # pythonw(スタートアップ起動)では sys.stdin が None になるため必ず存在確認する
    if not channel and sys.stdin is not None and sys.stdin.isatty():
        # スタートアップ自動起動(pythonw.exe)には対話コンソールが無いため、その場合は入力を求めない
        channel = input("監視するTwitchチャンネル名を入力してください: ").strip().lower()
        db.set_setting("channel_name", channel)

    url = f"http://{config.FLASK_HOST}:{config.FLASK_PORT}"

    if channel:
        client = AnonIrcClient(channel, on_line, status_callback=state.set_status)
        irc_thread = threading.Thread(target=client.run_forever, daemon=True)
        irc_thread.start()
        print(f"チャンネル「{channel}」を監視しています。")
        notifier.notify_info("発送台帳を起動しました", f"「{channel}」のチャット監視を開始しました。")
    else:
        print("チャンネル名が未設定です。設定画面から登録してください。")
        notifier.notify_info(
            "発送台帳を起動しました",
            "チャンネル名が未設定です。設定画面から登録してください。",
        )

    print(f"管理画面: {url}")
    app = create_app()
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, use_reloader=False)


if __name__ == "__main__":
    main()
