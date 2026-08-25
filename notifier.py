"""Windowsデスクトップ通知。

通知は「消えるまで残す(常時表示)」を既定とし、見逃しを防ぐ。
Windowsのトースト通知XMLを直接組み立ててPowerShell経由で表示する
(winotifyでは常時表示に必要な scenario 指定ができないため)。
通知が使えない環境ではコンソール出力にフォールバックする。
"""

import html
import subprocess
import sys

import config

APP_ID = "配信サポートツール"
# 通知のクリック先。httpではなく独自スキームを使うことで、
# 既定ブラウザの新規タブではなく「アプリの専用ウィンドウの前面化」につながる
ADMIN_URL = "tokutendaicho://open"

_IS_WINDOWS = sys.platform == "win32"

# scenario="reminder" にすると、ユーザーが閉じるまで通知が画面に残り続ける
_PS_TEMPLATE = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$Template = @"
<toast {launch_attr} {scenario_attr}>
    <visual>
        <binding template="ToastText02">
            <text id="1"><![CDATA[{title}]]></text>
            <text id="2"><![CDATA[{msg}]]></text>
        </binding>
    </visual>
    <actions>
        <action content="管理画面を開く" activationType="protocol" arguments="{url}" />
        <action content="閉じる" arguments="dismiss" activationType="system" />
    </actions>
</toast>
"@

$SerializedXml = New-Object Windows.Data.Xml.Dom.XmlDocument
$SerializedXml.LoadXml($Template)
$Toast = [Windows.UI.Notifications.ToastNotification]::new($SerializedXml)
$Notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}")
$Notifier.Show($Toast);
"""


def notify_milestone(login, kind, threshold):
    kind_label = "通算" if kind == "cumulative" else "連続"
    title = f"🎉 {kind_label}{threshold}ヶ月到達"
    msg = f"{login} さんが{kind_label}{threshold}ヶ月に到達しました"
    notify_info(title, msg, launch=ADMIN_URL)


def _ps_safe(text):
    """PowerShellの文字列内で意味を持つ記号を無害化する(視聴者名など外部入力への保険)。"""
    return str(text).replace("`", "'").replace("$", "").replace('"', "'")


def notify_info(title, msg, launch=None, persistent=None):
    """通知を表示する。persistent=Trueなら閉じるまで残す(既定は設定に従う)。"""
    if persistent is None:
        persistent = _persistent_enabled()

    if not _IS_WINDOWS:
        print(f"[通知] {title}: {msg}")
        return

    title = _ps_safe(title)
    msg = _ps_safe(msg)
    launch = launch or ADMIN_URL
    script = _PS_TEMPLATE.format(
        launch_attr=f'activationType="protocol" launch="{html.escape(launch, quote=True)}"',
        scenario_attr='scenario="reminder"' if persistent else 'duration="long"',
        title=title,
        msg=msg,
        url=html.escape(launch, quote=True),
        app_id=APP_ID,
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"[通知エラー] {e}: {title} / {msg}")


def clear_all():
    """このアプリが出した通知をすべて消す(終了時・更新時・起動時の残留掃除に使う)。"""
    if not _IS_WINDOWS:
        return
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] > $null\n"
        f'[Windows.UI.Notifications.ToastNotificationManager]::History.Clear("{APP_ID}")'
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def _persistent_enabled():
    # dbのimportを関数内で行い、モジュール間の循環参照を避ける
    try:
        import db

        return db.is_notify_persistent()
    except Exception:
        return True
