# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec dong goi Python runtime cua ERIS thanh 1 thu muc 'eris-runtime'
(onedir) co eris-runtime.exe. Tester KHONG can cai Python hay pip.

Build:
    pip install pyinstaller
    pyinstaller build/eris_runtime.spec --noconfirm

Ket qua: dist/eris-runtime/  (copy ca thu muc nay vao <ban export Godot>/runtime/)

Ghi chu: source .py se thanh .pyc trong bundle (kho doc, khong phai ma hoa that).
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

PROJECT_ROOT = os.path.abspath(os.getcwd())
SRC = os.path.join(PROJECT_ROOT, "src")

datas = []
binaries = []
hiddenimports = []

# Cac package nang/co data runtime: gom het de tranh thieu file luc chay.
for pkg in ("transformers", "tokenizers", "torch", "safetensors", "huggingface_hub", "regex"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Embedding model di kem repo (neu co).
_model_dir = os.path.join(SRC, "paraphrase-multilingual-MiniLM-L12-v2")
if os.path.isdir(_model_dir):
    datas.append((_model_dir, "paraphrase-multilingual-MiniLM-L12-v2"))

# Cac file config mac dinh (khong phai secrets) de runtime co default hop ly.
for cfg in ("focus_context_config.json", "goal_profiles.json", "ni_focus_config.json"):
    p = os.path.join(PROJECT_ROOT, cfg)
    if os.path.isfile(p):
        datas.append((p, "."))

# vision.py import cac package top-level trong src/ (actions, cortex, runtime, ...)
# nen them src vao pathex va gom submodules.
for pkg in ("cortex", "runtime", "memory", "memory_core", "llm", "actions",
            "parser_grounding", "transport", "telemetry", "core", "domain"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    [os.path.join(SRC, "vision.py")],
    pathex=[SRC, PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="eris-runtime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,   # giu console de doc log/loi khi test; doi thanh False khi phat hanh
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="eris-runtime",
)
