@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  特典台帳 インストーラー ビルド
echo ============================================
echo.

REM --- PyInstallerの確認 ---
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstallerをインストールします...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo PyInstallerのインストールに失敗しました。
        pause
        exit /b 1
    )
)

REM --- Inno Setup(ISCC.exe)の確認 ---
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo Inno Setup 6 が見つかりません。
    echo https://jrsoftware.org/isdl.php からインストールしてください。
    echo winget install JRSoftware.InnoSetup でも導入できます
    pause
    exit /b 1
)

REM --- バージョン番号をconfig.pyから取得 ---
for /f "usebackq delims=" %%V in (`python -c "import config; print(config.APP_VERSION)"`) do set "VERSION=%%V"
echo バージョン: %VERSION%
echo.

REM --- 前回のビルド成果物を削除 ---
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist dist_installer rmdir /s /q dist_installer

echo [1/2] PyInstallerでexe化しています...
python -m PyInstaller installer\tokuten.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 (
    echo exe化に失敗しました。
    pause
    exit /b 1
)

echo.
echo [2/2] Inno Setupでインストーラーを作成しています...
"%ISCC%" /DAppVersion=%VERSION% installer\setup.iss
if errorlevel 1 (
    echo インストーラー作成に失敗しました。
    pause
    exit /b 1
)

echo.
echo ============================================
echo  完了しました!
echo  dist_installer\TokutenDaicho-Setup-v%VERSION%.exe
echo ============================================
pause
