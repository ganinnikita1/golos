# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller-спецификация «Голоса» (one-dir, без консоли).

Сборка: powershell -ExecutionPolicy Bypass -File packaging\build.ps1
Результат: dist\Golos\Golos.exe
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH даёт PyInstaller
ASSETS = ROOT / "golos" / "assets"

datas = [(str(ASSETS), "golos/assets")] if ASSETS.exists() else []
MANIFEST = ROOT / "golos" / "asr" / "model_manifest.json"
if MANIFEST.exists():
    datas.append((str(MANIFEST), "golos/asr"))
datas += collect_data_files("sentencepiece")

hiddenimports = [
    "golos",
    "golos.app",
    "golos.cli",
    "golos.ui",
    "golos.ui.app_ui",
    "golos.ui.tray",
    "golos.ui.overlay",
    "golos.ui.settings",
    "golos.ui.history",
    "golos.ui.onboarding",
    "golos.ui.theme",
    "golos.asr.registry",
    "golos.asr.gigaam_engine",
    "golos.asr.whisper_engine",
    "golos.asr.cloud_engine",
    "golos.text.postprocess",
    "golos.text.llm",
    "golos.text.dictionary",
    "golos.inject.injector",
    "golos.hotkey.hook",
    # torch/transformers тянут много ленивых импортов
    "transformers.dynamic_module_utils",
    "transformers.models.auto",
    "huggingface_hub",
    "hydra",
    "omegaconf",
    "soundfile",
    "sounddevice",
    "PySide6.QtWidgets",
    "PySide6.QtGui",
    "PySide6.QtCore",
    "PySide6.QtNetwork",
]
hiddenimports += collect_submodules("golos", filter=lambda name: name.startswith("golos."))

excludes = [
    # не нужные тяжёлые части PySide6 и научного стека
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtDesigner",
    "PySide6.QtTest", "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "matplotlib", "scipy", "pandas", "tkinter", "notebook", "IPython", "jupyter",
    "torchvision", "pytest", "setuptools", "pip",
    "onnxruntime.tools", "Cython", "lib2to3", "pydoc_data",
]

analysis = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Golos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "golos.ico") if (ASSETS / "golos.ico").exists() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Golos",
)
