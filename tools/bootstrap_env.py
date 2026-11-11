"""Bootstrap the Golos venv: CPU torch, transformers stack, audio + UI deps.

Runs twice so the PyTorch CPU index only serves torch/torchaudio.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PYPI = "https://pypi.org/simple"


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy",
    ):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(args: list[str]) -> int:
    print("+", " ".join(args), flush=True)
    code = subprocess.call(args, env=clean_env())
    print("exit:", code, flush=True)
    return code


def main() -> int:
    if not os.path.exists(PY):
        print("venv missing:", PY)
        return 2

    steps = [
        # torch CPU wheels only
        [PY, "-m", "pip", "install", "--index-url", CPU_INDEX,
         "torch==2.8.0", "torchaudio==2.8.0"],
        # ASR + audio + UI runtime
        [PY, "-m", "pip", "install", "--index-url", PYPI,
         "transformers==4.57.1", "sentencepiece", "huggingface_hub",
         "hydra-core", "omegaconf",
         "sounddevice", "soundfile", "numpy",
         "PySide6-Essentials", "Pillow",
         "faster-whisper", "psutil"],
        # test + packaging tooling
        [PY, "-m", "pip", "install", "--index-url", PYPI, "pytest", "pyinstaller"],
    ]
    for step in steps:
        code = run(step)
        if code != 0:
            return code
    run([PY, "-c", "import torch, transformers, sounddevice, PySide6; print('OK', torch.__version__, transformers.__version__)"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
