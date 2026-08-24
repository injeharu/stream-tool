# -*- mode: python ; coding: utf-8 -*-
"""PyInstallerのビルド設定。build_installer.bat から呼ばれる。

onedir形式(フォルダ+exe)でビルドする。onefileより起動が速く、
セキュリティソフトの誤検知も少ないため。
"""

import os

block_cipher = None

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), ".."))

a = Analysis(
    [os.path.join(PROJECT_DIR, "app.py")],
    pathex=[PROJECT_DIR],
    binaries=[],
    datas=[
        (os.path.join(PROJECT_DIR, "web", "templates"), "web/templates"),
        (os.path.join(PROJECT_DIR, "web", "static"), "web/static"),
    ],
    hiddenimports=["winotify"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TokutenDaicho",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # ノーコンソール(黒い窓を出さない)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TokutenDaicho",
)
