"""Twitchアカウント連携(任意機能)。

チャット監視だけでは分からない情報(サブスクしている人の名簿・ギフト状態など)を
公式APIから取得する。連携しなくてもツールの全機能は動くため、あくまで追加機能。

認証は Device Code Flow を使う。利用者は画面に出るコードを
Twitchのページに入力するだけでよく、開発者登録は不要。
クライアントシークレットを持たない方式なので、配布物に秘密情報を含めずに済む。
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config
import db

DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API_BASE = "https://api.twitch.tv/helix/"

# 取得したい情報に必要な権限。
# 実際に動作を確認できたものだけに絞っている
# (ハイプトレインはAPI廃止済み、ポイント引き換えは自作の報酬しか読めないため除外)
SCOPES = " ".join([
    "channel:read:subscriptions",   # サブスクしている人の名簿
    "moderator:read:followers",     # フォロワー一覧(フォロー日時つき)
    "bits:read",                    # ビッツの公式ランキング
    "channel:read:goals",           # 配信の目標(フォロワー数・サブスク数)
])

_auth_lock = threading.Lock()
_pending = None  # 認証待ちの情報(コードと期限)


class TwitchApiError(Exception):
    pass


# ---------- 認証 ----------

def start_device_auth():
    """認証を開始し、利用者に見せるコードとURLを返す。"""
    data = urllib.parse.urlencode({"client_id": config.TWITCH_CLIENT_ID, "scopes": SCOPES}).encode()
    req = urllib.request.Request(DEVICE_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            result = json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        raise TwitchApiError(f"認証を開始できませんでした: {e.code}")
    except OSError:
        # 通信できない(ネット切断・Twitch障害など)。URLErrorもOSErrorに含まれる
        raise TwitchApiError("Twitchに接続できませんでした。通信環境をご確認ください")

    global _pending
    with _auth_lock:
        _pending = {
            "device_code": result["device_code"],
            "interval": result.get("interval", 5),
            "expires_at": time.time() + result.get("expires_in", 1800),
        }
    # 待機は裏で行う(画面が固まらないように)
    threading.Thread(target=_poll_for_token, daemon=True).start()

    return {
        "user_code": result["user_code"],
        "verification_uri": result["verification_uri"],
        "expires_in": result.get("expires_in", 1800),
    }


def _poll_for_token():
    """利用者がTwitchで承認するのを待ち、承認されたらトークンを保存する。"""
    with _auth_lock:
        pending = dict(_pending) if _pending else None
    if not pending:
        return

    while time.time() < pending["expires_at"]:
        data = urllib.parse.urlencode({
            "client_id": config.TWITCH_CLIENT_ID,
            "scopes": SCOPES,
            "device_code": pending["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode()
        req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                token = json.loads(res.read().decode())
            _save_token(token)
            # 承認された直後にアカウント名も控えておく(画面表示と取り込み時の照合に使う)
            try:
                fetch_me()
            except Exception:
                pass
            _clear_pending(pending["device_code"])
            print("[Twitch連携] 認証が完了しました")
            return
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if "authorization_pending" in body:
                time.sleep(pending["interval"])
                continue
            print(f"[Twitch連携] 認証に失敗しました: {body[:200]}")
            _clear_pending(pending["device_code"])
            return
        except Exception:
            time.sleep(pending["interval"])
    _clear_pending(pending["device_code"])


def _clear_pending(device_code=None):
    """認証待ちを解除する。やり直しで新しい認証が始まっていた場合は、
    古い待機処理が新しい方を消してしまわないようコードの一致を確認する。"""
    global _pending
    with _auth_lock:
        if device_code is None or (_pending and _pending.get("device_code") == device_code):
            _pending = None


def is_authenticating():
    with _auth_lock:
        return _pending is not None


def _save_token(token):
    """トークンを保存する。保存先はデータと同じ場所で、配布物やGitには含まれない。"""
    db.set_setting("twitch_access_token", token.get("access_token", ""))
    db.set_setting("twitch_refresh_token", token.get("refresh_token", ""))
    db.set_setting("twitch_token_expires_at", str(int(time.time() + token.get("expires_in", 0))))


def is_linked():
    return bool(db.get_setting("twitch_access_token", ""))


def unlink():
    """連携を解除する(保存したトークンを消す)。"""
    for key in ("twitch_access_token", "twitch_refresh_token", "twitch_token_expires_at",
                "twitch_user_id", "twitch_user_login", "twitch_broadcaster_type"):
        db.set_setting(key, "")


_refresh_lock = threading.Lock()


def _refresh_token():
    """期限切れのトークンを更新する。更新用のトークンは一度きりしか使えないため、
    同時に2箇所から更新すると片方が必ず失敗する。ロックで一度にひとつだけ実行し、
    直前に他の処理が更新を終えていたらそれをそのまま使う。"""
    with _refresh_lock:
        try:
            expires_at = int(db.get_setting("twitch_token_expires_at", "0"))
        except (TypeError, ValueError):
            expires_at = 0
        if time.time() < expires_at - 300:
            return True  # 待っている間に別の処理が更新を済ませていた

        return _refresh_token_locked()


def _refresh_token_locked():
    refresh = db.get_setting("twitch_refresh_token", "")
    if not refresh:
        return False
    data = urllib.parse.urlencode({
        "client_id": config.TWITCH_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            token = json.loads(res.read().decode())
        _save_token(token)
        return True
    except Exception as e:
        print(f"[Twitch連携] トークンの更新に失敗しました: {e}")
        return False


def _valid_token():
    if not is_linked():
        return None
    expires_at = db.get_setting("twitch_token_expires_at", "0")
    try:
        expires_at = int(expires_at)
    except ValueError:
        expires_at = 0
    # 期限の5分前には更新しておく
    if time.time() > expires_at - 300:
        if not _refresh_token():
            return None
    return db.get_setting("twitch_access_token", "")


# ---------- API呼び出し ----------

def _api(path, retry_on_auth_error=True):
    token = _valid_token()
    if not token:
        raise TwitchApiError("Twitchと連携していません")

    req = urllib.request.Request(
        API_BASE + path,
        headers={"Authorization": f"Bearer {token}", "Client-Id": config.TWITCH_CLIENT_ID},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code == 401 and retry_on_auth_error and _refresh_token():
            return _api(path, retry_on_auth_error=False)
        try:
            message = json.loads(body).get("message", body)
        except Exception:
            message = body
        raise TwitchApiError(f"{e.code}: {message}")
    except OSError:
        # 通信できない(ネット切断・Twitch障害など)
        raise TwitchApiError("Twitchに接続できませんでした。通信環境をご確認ください")


def fetch_me():
    """連携したアカウントの情報を取得して保存する。"""
    data = _api("users")
    rows = data.get("data", [])
    if not rows:
        raise TwitchApiError("アカウント情報を取得できませんでした")
    user = rows[0]
    db.set_setting("twitch_user_id", user["id"])
    db.set_setting("twitch_user_login", user["login"])
    db.set_setting("twitch_broadcaster_type", user.get("broadcaster_type", ""))
    return user


def _broadcaster_id():
    uid = db.get_setting("twitch_user_id", "")
    if not uid:
        uid = fetch_me()["id"]
    return uid


def fetch_all(path_template, limit=100, max_pages=50):
    """ページ送りが必要なAPIをまとめて取得する。"""
    uid = _broadcaster_id()
    rows = []
    cursor = None
    for _ in range(max_pages):
        path = path_template.format(uid=uid, first=limit)
        if cursor:
            path += f"&after={urllib.parse.quote(cursor)}"
        data = _api(path)
        rows.extend(data.get("data", []))
        cursor = (data.get("pagination") or {}).get("cursor")
        if not cursor:
            break
    return rows


def fetch_subscribers():
    """サブスクしている人の名簿。ティア・ギフト状態・贈り主が分かる。"""
    return fetch_all("subscriptions?broadcaster_id={uid}&first={first}")


def fetch_followers():
    """フォロワー一覧(フォロー日時つき)。"""
    return fetch_all("channels/followers?broadcaster_id={uid}&first={first}")


def fetch_bits_leaderboard(period="all", count=100):
    """ビッツの支援ランキング(Twitch公式の集計)。
    チャット監視では拾えない過去分も含む正確な総額が得られる。"""
    data = _api(f"bits/leaderboard?count={count}&period={period}")
    return data.get("data", [])


def fetch_goals():
    """配信の目標(フォロワー数・サブスク数など)の進捗。"""
    uid = _broadcaster_id()
    data = _api(f"goals?broadcaster_id={uid}")
    return data.get("data", [])


def sync_all():
    """連携で取れる情報をまとめて取り込む。取得できたものだけ保存する。"""
    channel = db.current_channel()
    if not channel:
        raise TwitchApiError("先に監視するチャンネルを設定してください")

    user = fetch_me()
    # 連携したアカウントと監視中のチャンネルが違うと、取れるのは別チャンネルの情報になる
    if user["login"].lower() != channel.lower():
        raise TwitchApiError(
            f"連携したアカウント({user['login']})と監視中のチャンネル({channel})が違います。"
            "自分のチャンネルを設定してから取り込んでください。"
        )

    result = {"subscribers": 0, "followers": 0, "bits": 0, "goals": 0, "errors": []}

    try:
        subs = fetch_subscribers()
        db.replace_twitch_subscribers(channel, subs)
        result["subscribers"] = len(subs)
    except TwitchApiError as e:
        result["errors"].append(f"サブスク名簿: {e}")

    try:
        followers = fetch_followers()
        db.replace_twitch_followers(channel, followers)
        result["followers"] = len(followers)
    except TwitchApiError as e:
        result["errors"].append(f"フォロワー: {e}")

    try:
        bits = fetch_bits_leaderboard()
        db.replace_twitch_bits(channel, bits)
        result["bits"] = len(bits)
    except TwitchApiError as e:
        result["errors"].append(f"ビッツ: {e}")

    try:
        goals = fetch_goals()
        db.set_setting("twitch_goals", json.dumps(goals, ensure_ascii=False))
        result["goals"] = len(goals)
    except TwitchApiError as e:
        result["errors"].append(f"目標: {e}")

    return result
