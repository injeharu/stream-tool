@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo 配信サポートツール(特典台帳)を起動します...
echo.

python app.py
if errorlevel 1 (
    echo.
    echo 起動に失敗しました。
    echo Pythonがインストールされていない可能性があります。
    echo https://www.python.org/downloads/ からインストールしてください。
    echo その際「Add python.exe to PATH」に必ずチェックを入れてください。
    echo.
    pause
)
