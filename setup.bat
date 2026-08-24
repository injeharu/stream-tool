@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  配信サポートツール(特典台帳) 初回セットアップ
echo ============================================
echo.
echo 必要な部品をインストールします。少しお待ちください...
echo.

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo インストールに失敗しました。
    echo Pythonがインストールされていない可能性があります。
    echo https://www.python.org/downloads/ からインストールしてください。
    echo その際「Add python.exe to PATH」に必ずチェックを入れてください。
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  セットアップが完了しました!
echo  次回からは start.bat をダブルクリックしてください。
echo ============================================
echo.
pause
