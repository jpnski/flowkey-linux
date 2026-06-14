# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import os
import sysconfig

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(os.getcwd()).resolve()
SCRIPTS = ROOT / "scripts"


def _python_shared_library() -> list[tuple[str, str]]:
    libdir = sysconfig.get_config_var("LIBDIR")
    if not libdir:
        return []

    for libname in (
        sysconfig.get_config_var("INSTSONAME"),
        sysconfig.get_config_var("LDLIBRARY"),
    ):
        if not libname:
            continue
        candidate = Path(libdir) / libname
        if candidate.exists():
            return [(str(candidate), ".")]

    return []


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
    "version",
    "tools",
    "notes",
    "textual.widgets._tab_pane",
]
hiddenimports += collect_submodules("tui")
hiddenimports += collect_submodules("pynput")
hiddenimports += collect_submodules("evdev")
hiddenimports += collect_submodules("dasbus")
hiddenimports += collect_submodules("pystray")

binaries = _python_shared_library()

datas = [
    (str(SCRIPTS / "config.seed.json"), "."),
    (str(SCRIPTS / "assets" / "flowkey.png"), "assets"),
    (str(SCRIPTS / "assets" / "flowkey.ico"), "assets"),
]


a = Analysis(
    [str(SCRIPTS / "flowkey.py")],
    pathex=[str(SCRIPTS)],
    binaries=binaries,
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
    exclude_binaries=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
