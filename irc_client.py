"""Twitch IRCへの匿名(読み取り専用)接続。標準ライブラリのみで実装(配布時の依存を減らすため)。

チャンネル名は実行中に set_channel() で変更でき、その場合は現在の接続を閉じて
新しいチャンネルへ繋ぎ直す(ツールの再起動なしで切り替えるため)。
"""

import socket
import ssl
import random
import threading
import time

import config


class _ChannelChanged(Exception):
    """チャンネル切り替えのために意図的に接続を切ったことを示す(エラー扱いしない)。"""


class AnonIrcClient:
    def __init__(self, channel, on_line, status_callback=None):
        self.channel = (channel or "").lower().lstrip("#")
        self.on_line = on_line
        self.status_callback = status_callback or (lambda s: None)
        self._stop = threading.Event()
        self._channel_changed = threading.Event()
        self._retry_delay = 5

    def stop(self):
        self._stop.set()
        self._channel_changed.set()

    def set_channel(self, new_channel):
        """実行中に監視対象チャンネルを切り替える(再起動不要)。"""
        new_channel = (new_channel or "").lower().lstrip("#")
        if new_channel == self.channel:
            return
        self.channel = new_channel
        self._channel_changed.set()

    def run_forever(self):
        while not self._stop.is_set():
            if not self.channel:
                # チャンネル未設定の間は接続せず待機する
                self.status_callback("disconnected")
                self._channel_changed.wait(timeout=2)
                self._channel_changed.clear()
                continue
            try:
                self.status_callback("connecting")
                self._channel_changed.clear()
                self._connect_and_listen()
            except _ChannelChanged:
                print(f"[IRC] チャンネルを切り替えます: #{self.channel}")
            except Exception as e:
                print(f"[IRC] 切断されました: {e}")
                self.status_callback("disconnected")
                if self._stop.is_set():
                    break
                time.sleep(self._retry_delay)
                self._retry_delay = min(self._retry_delay * 2, 60)

    def _connect_and_listen(self):
        raw_sock = socket.create_connection((config.IRC_SERVER, config.IRC_PORT), timeout=30)
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw_sock, server_hostname=config.IRC_SERVER)
        # 短めのタイムアウトにして、定期的にチャンネル切り替え要求をチェックできるようにする
        sock.settimeout(10)
        try:
            nick = f"justinfan{random.randint(10000, 99999)}"
            self._send(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
            self._send(sock, f"NICK {nick}")
            self._send(sock, f"JOIN #{self.channel}")
            self.status_callback("connected")
            # 接続に成功したら再接続の待ち時間をリセットする(長期稼働で待ちが伸び続けないように)
            self._retry_delay = 5

            buffer = ""
            while not self._stop.is_set():
                if self._channel_changed.is_set():
                    raise _ChannelChanged()
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    raise ConnectionError("サーバーから接続が切断されました")
                buffer += data.decode("utf-8", errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    self._handle_line(sock, line)
        finally:
            sock.close()

    def _handle_line(self, sock, line):
        if line.startswith("PING"):
            self._send(sock, line.replace("PING", "PONG", 1))
            return
        # 1行の処理エラーで接続を巻き込まない(サブスク検知を止めないことを最優先)
        try:
            self.on_line(line)
        except Exception as e:
            print(f"[IRC] メッセージ処理中にエラー(接続は維持): {e}")
            print(f"  対象行: {line[:200]}")

    def _send(self, sock, msg):
        sock.sendall((msg + "\r\n").encode("utf-8"))
