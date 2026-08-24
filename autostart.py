"""WindowsのPC起動時自動起動(スタートアップ登録)を扱うモジュール。

HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run にユーザー単位で登録する。
管理者権限は不要。
- exe版: exe自身をそのまま登録する
- 開発版(python app.py): pythonw.exe(コンソール窓なし)で起動するようにする
"""

import os
import sys

import config

try:
    import winreg
    _AVAILABLE = True
except ImportError:
    # Windows以外(開発・テスト時)では機能を無効化する
    _AVAILABLE = False

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TokutenDaicho"
_OLD_VALUE_NAME = "StreamShippingTool"  # 旧名(発送台帳時代)の登録が残っていれば掃除する

APP_PY_PATH = os.path.join(config.RESOURCE_DIR, "app.py")


def _pythonw_path():
    """コンソール窓を出さないpythonw.exeのパスを返す。無ければ通常のpython.exeを使う。"""
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if os.path.exists(pythonw):
        return pythonw
    return sys.executable


def _command():
    if config.IS_FROZEN:
        return f'"{sys.executable}"'
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
        try:
            winreg.DeleteValue(key, _OLD_VALUE_NAME)
        except FileNotFoundError:
            pass


def disable():
    if not _AVAILABLE:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
