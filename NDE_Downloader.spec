# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# customtkinter — wajib dibawa semua (tema, font, aset)
tmp = collect_all('customtkinter')
datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]

# playwright — bawa package Python-nya (driver), browser pakai yg sudah terinstall di sistem
tmp = collect_all('playwright')
datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]

# pandas & openpyxl (untuk mode Excel)
tmp = collect_all('pandas')
datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]
tmp = collect_all('openpyxl')
datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        'playwright',
        'playwright.sync_api',
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'openpyxl.styles.builtins',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'IPython', 'jupyter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NDE_Downloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,    # tidak tampil window konsol
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
