"""生のIRCログをファイルから読み込んでパーサ・DB・通知処理に流し込むテストモード。

使い方:
    python tools/replay.py tests/sample_lines.txt
    python tools/replay.py tests/sample_lines.txt --no-notify
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import irc_parser
import milestone
import ranking


def replay(path, notify=True):
    db.init_db()
    ranking.reload_keyword_cache()

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    processed = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parsed = irc_parser.parse_line(line)
        if not parsed:
            print(f"[パース失敗] {line[:60]}...")
            continue

        if parsed.command == "USERNOTICE":
            milestone.handle_usernotice(parsed, notify=notify)
            print(f"[USERNOTICE] {parsed.login} msg-id={parsed.tags.get('msg-id')}")
        elif parsed.command == "PRIVMSG":
            milestone.handle_privmsg(parsed)
            print(f"[PRIVMSG] {parsed.login}: {parsed.text}")
        else:
            print(f"[未対応コマンド] {parsed.command}")

        processed += 1

    print(f"\n{processed}行を処理しました。")
    print(f"特典待ち件数: {db.count_pending_milestones()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IRC生ログの再生テストツール")
    parser.add_argument("path", help="生ログファイルのパス")
    parser.add_argument("--no-notify", action="store_true", help="デスクトップ通知を出さない")
    args = parser.parse_args()

    replay(args.path, notify=not args.no_notify)
