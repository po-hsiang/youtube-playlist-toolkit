# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包設定：將歌單搜尋工具打包為單一執行檔。
# 執行：pyinstaller playlist_search.spec
# 注意：執行檔會從「執行檔所在目錄」讀取 .env 與 secrets/，
#       部署時請將 .env 與 secrets/ 放在 exe 旁邊。


a = Analysis(
    ['youtube_toolkit/playlist_search.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='playlist_search',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
