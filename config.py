import os
import sys

# exe化(PyInstaller)されているかどうか。
# 開発時(python app.py)はFalse、ビルド後のexe実行時にTrueになる。
IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    # exe自体が置かれた場所(テンプレート等の同梱リソースはここを基準に探す)
    RESOURCE_DIR = sys._MEIPASS if hasattr(sys, "_MEIPASS") else os.path.dirname(sys.executable)
    # インストール先(Program Files等)は書き込み不可のことがあるため、
    # データは必ずユーザーごとの書き込み可能な場所(%APPDATA%)に置く
    DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TokutenDaicho")
    os.makedirs(DATA_DIR, exist_ok=True)
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = RESOURCE_DIR

BASE_DIR = RESOURCE_DIR  # 後方互換(replay.py等が参照)
DB_PATH = os.path.join(DATA_DIR, "data.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
# オーバーレイのカスタム効果音の置き場(利用者が設定画面から登録する)
SOUND_DIR = os.path.join(DATA_DIR, "sounds")

# 登録できる音源の種類と上限
ALLOWED_SOUND_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus", ".webm"}
MAX_SOUND_BYTES = 10 * 1024 * 1024  # 10MB

DEFAULT_CUMULATIVE_THRESHOLDS = [6, 12, 24]
DEFAULT_STREAK_THRESHOLDS = [6, 12, 24]

FLASK_HOST = "127.0.0.1"
FLASK_PORT = 8712

IRC_SERVER = "irc.chat.twitch.tv"
IRC_PORT = 6697

ALL_TIERS = ["1000", "2000", "3000", "Prime"]
TIER_LABELS = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3", "Prime": "Prime"}

RANKING_TOP_N = 20

APP_VERSION = "1.3.7"
GITHUB_REPO = "injeharu/stream-tool"  # owner/repo(更新確認とリリース作成の両方で使用)
UPDATE_CHECK_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# 起動時に1回+この間隔ごとに新バージョンを確認する
UPDATE_CHECK_INTERVAL_SECONDS = 4 * 60 * 60
