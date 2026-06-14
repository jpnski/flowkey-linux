# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(os.getcwd()).resolve()
SCRIPTS = ROOT / "scripts"


hiddenimports = [
    "flowkey",
    "launcher",
    "daemon",
    "engine",
    "install",
    "listener",
    "tray",
    "config",
    "flm_server",
    "llm_client",
    "loopback_http",
    "notify",
    "paths",
    "pull",
    "subprocess_util",
    "telemetry",
    "tools",
    "notes",
]
hiddenimports += collect_submodules("tui")

datas = [
    (str(SCRIPTS / "config.seed.json"), "_internal"),
    (str(SCRIPTS / "assets" / "flowkey.png"), "_internal/assets"),
    (str(SCRIPTS / "assets" / "flowkey.ico"), "_internal/assets"),
]


a = Analysis(
    [str(SCRIPTS / "flowkey.py")],
    pathex=[str(SCRIPTS)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="flowkey",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    exclude_binaries=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="flowkey",
)
