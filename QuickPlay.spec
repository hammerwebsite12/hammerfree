# -*- mode: python ; coding: utf-8 -*-
# QuickPlay 2.0 — unified PlayZip + AnkerGames (dual-store).

import os

block_cipher = None
SPECPATH = os.path.dirname(os.path.abspath(SPEC))
_runtime_tmp = os.path.join("%LOCALAPPDATA%", "QuickPlay", "_runtime")

a = Analysis(
    [os.path.join(SPECPATH, "main.py")],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        ("web", "web"),
        ("vendor/7zip", "vendor/7zip"),
        ("quickplay.ico", "."),
    ],
    hiddenimports=[
        "store_manager",
        "anker",
        "anker.anker_api",
        "anker.gate_bridge",
        "anker.gate_resolver",
        "anker.gate_resolver_worker",
        "anker.anker_game_details",
        "anker.config",
        "client_secrets",
        "license_manager",
        "library_manager",
        "library_scan",
        "library_artwork",
        "trainer_service",
        "device_fingerprint",
        "hardware_snapshot",
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
    icon="quickplay.ico",
)
