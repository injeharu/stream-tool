import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data.db")

DEFAULT_CUMULATIVE_THRESHOLDS = [6, 12, 24]
DEFAULT_STREAK_THRESHOLDS = [6, 12, 24]

FLASK_HOST = "127.0.0.1"
FLASK_PORT = 8712

IRC_SERVER = "irc.chat.twitch.tv"
IRC_PORT = 6697

FORECAST_MONTHS_AHEAD = 3

ALL_TIERS = ["1000", "2000", "3000", "Prime"]
TIER_LABELS = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3", "Prime": "Prime"}

RANKING_TOP_N = 20

APP_VERSION = "1.0.0"
UPDATE_CHECK_URL = "https://api.github.com/repos/injeharu/stream-tool/releases/latest"
UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

