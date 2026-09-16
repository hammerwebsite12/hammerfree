# -*- mode: python ; coding: utf-8 -*-
# QuickPlay protected release — PyArmor obfuscated entry + PyInstaller onefile EXE.

import os
import glob

block_cipher = None
SPECPATH = os.path.dirname(os.path.abspath(SPEC))
obf_dir = os.path.join(SPECPATH, "build", "pyarmor")
rt_pkg = os.path.join(obf_dir, "pyarmor_runtime_000000")
_runtime_tmp = os.path.join("%LOCALAPPDATA%", "QuickPlay", "_runtime")

rt_datas = []
if os.path.isdir(rt_pkg):
    for path in glob.glob(os.path.join(rt_pkg, "**", "*"), recursive=True):
        if os.path.isfile(path):
            rel = os.path.relpath(path, obf_dir).replace("\\", "/")
            rt_datas.append((path, os.path.dirname(rel) or "."))

a = Analysis(
    [os.path.join(SPECPATH, "main.py")],
    pathex=[obf_dir, SPECPATH],
    binaries=[],
    # Do not bundle anker/ as loose datas — onefile extracts datas to %LOCALAPPDATA%
    # and would ship FORMULA.md, README.md, and other dev docs. Collect via hiddenimports.
    datas=[("web", "web"), ("vendor/7zip", "vendor/7zip"), ("quickplay.ico", ".")] + rt_datas,
    hiddenimports=[
        "pyarmor_runtime_000000",
        "worker_tls",
        "gamepad_input",
        "client_secrets",
        "store_manager",
        "anker",
        "anker.anker_api",
        "anker.gate_bridge",
        "anker.gate_resolver",
        "anker.gate_resolver_worker",
        "anker.anker_game_details",
        "anker.config",
        "storage_requirements",
        "hwid_obfuscation",
        "license_manager",
        "cover_cache",
        "banner_cache",
        "native_dialog",
        "tkinter",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["sqlite3"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="QuickPlay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=_runtime_tmp,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    manifest="playzip.manifest",
    icon=os.path.join(SPECPATH, "build", "quickplay-pyinstaller.ico"),
)
