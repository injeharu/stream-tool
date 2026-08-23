"""Twitch IRCへの匿名(読み取り専用)接続。標準ライブラリのみで実装(配布時の依存を減らすため)。"""

import socket
import ssl
import random
import threading
import time

import config


class AnonIrcClient:
    def __init__(self, channel, on_line, status_callback=None):
        self.channel = channel.lower().lstrip("#")
        self.on_line = on_line
        self.status_callback = status_callback or (lambda s: None)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run_forever(self):
        delay = 5
        while not self._stop.is_set():
            try:
                self.status_callback("connecting")
                self._connect_and_listen()
                delay = 5
            except Exception as e:
                print(f"[IRC] 切断されました: {e}")
                self.status_callback("disconnected")
                if self._stop.is_set():
                    break
                time.sleep(delay)
                delay = min(delay * 2, 60)

    def _connect_and_listen(self):
        raw_sock = socket.create_connection((config.IRC_SERVER, config.IRC_PORT), timeout=30)
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw_sock, server_hostname=config.IRC_SERVER)
        sock.settimeout(300)
        try:
            nick = f"justinfan{random.randint(10000, 99999)}"
            self._send(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
            self._send(sock, f"NICK {nick}")
            self._send(sock, f"JOIN #{self.channel}")
            self.status_callback("connected")

            buffer = ""
            while not self._stop.is_set():
                data = sock.recv(4096)
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
        self.on_line(line)

    def _send(self, sock, msg):
        sock.sendall((msg + "\r\n").encode("utf-8"))
