"""WindowsのPC起動時自動起動(スタートアップ登録)を扱うモジュール。

HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run にユーザー単位で登録する。
管理者権限は不要。pythonw.exe(コンソール窓なし)で起動するようにする。
"""

import os
import sys

try:
    import winreg
    _AVAILABLE = True
except ImportError:
    # Windows以外(開発・テスト時)では機能を無効化する
    _AVAILABLE = False

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "StreamShippingTool"

APP_PY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")


def _pythonw_path():
    """コンソール窓を出さないpythonw.exeのパスを返す。無ければ通常のpython.exeを使う。"""
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if os.path.exists(pythonw):
        return pythonw
    return sys.executable


def _command():
    return f'"{_pythonw_path()}" "{APP_PY_PATH}"'


def is_available():
    return _AVAILABLE


def is_enabled():
    if not _AVAILABLE:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def enable():
    if not _AVAILABLE:
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())


def disable():
    if not _AVAILABLE:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
