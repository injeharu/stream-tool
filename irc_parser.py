"""Twitch IRC(タグ付き)の生ログ1行をパースするモジュール。標準ライブラリのみ使用。"""

from dataclasses import dataclass, field


@dataclass
class ParsedMessage:
    tags: dict
    prefix: str
    command: str
    params: list
    text: str
    login: str
    channel: str = ""


def _unescape_tag_value(value):
    # IRCv3タグのエスケープ解除。\\ を含む1パス処理でないと二重解除で壊れる。
    result = []
    i = 0
    n = len(value)
    while i < n:
        c = value[i]
        if c == "\\" and i + 1 < n:
            nxt = value[i + 1]
            if nxt == "s":
                result.append(" ")
            elif nxt == "n":
                result.append("\n")
            elif nxt == "r":
                result.append("\r")
            elif nxt == ":":
                result.append(";")
            elif nxt == "\\":
                result.append("\\")
            else:
                result.append(nxt)
            i += 2
        else:
            result.append(c)
            i += 1
    return "".join(result)


def parse_line(raw):
    if raw is None:
        return None
    raw = raw.rstrip("\r\n")
    if not raw:
        return None

    tags = {}
    rest = raw

    if rest.startswith("@"):
        tag_part, sep, rest2 = rest.partition(" ")
        if not sep:
            return None
        tag_part = tag_part[1:]
        rest = rest2
        for kv in tag_part.split(";"):
            if not kv:
                continue
            if "=" in kv:
                k, v = kv.split("=", 1)
                tags[k] = _unescape_tag_value(v)
            else:
                tags[kv] = ""

    prefix = ""
    if rest.startswith(":"):
        prefix, sep, rest = rest.partition(" ")
        prefix = prefix[1:]

    if " :" in rest:
        head, _, text = rest.partition(" :")
    else:
        head, text = rest, ""

    parts = head.split(" ") if head else []
    parts = [p for p in parts if p != ""]
    command = parts[0] if parts else ""
    params = parts[1:]

    login = tags.get("login", "")
    if not login and prefix:
        login = prefix.split("!")[0]
    login = login.lower()

    channel = ""
    for p in params:
        if p.startswith("#"):
            channel = p[1:]
            break

    return ParsedMessage(
        tags=tags,
        prefix=prefix,
        command=command,
        params=params,
        text=text,
        login=login,
        channel=channel,
    )
