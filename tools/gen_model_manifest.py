"""Генерация манифеста целостности модели GigaAM-v3.

Для каждой ревизии (e2e_rnnt, e2e_ctc, ctc, rnnt, ssl) фиксируем:
* commit SHA ревизии на Hugging Face (неизменяемая точка, в отличие от имени ветки);
* SHA256 каждого файла модели: для LFS-файлов хеш берётся из метаданных HF,
  для небольших обычных файлов считается по скачанной raw-версии на этом коммите.

Результат: golos/asr/model_manifest.json — его использует golos/asr/integrity.py,
чтобы не исполнять remote-код и не грузить веса, если они не совпали с ожидаемыми.

Запуск (нужен интернет):
    .venv\\Scripts\\python.exe tools\\gen_model_manifest.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO_ID = "ai-sage/GigaAM-v3"
VARIANTS = ["e2e_rnnt", "e2e_ctc", "ctc", "rnnt", "ssl"]
OUT = Path(__file__).resolve().parents[1] / "golos" / "asr" / "model_manifest.json"


def clean_env() -> None:
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                "https_proxy", "http_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "golos-manifest"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "golos-manifest"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def main() -> int:
    clean_env()
    manifest: dict[str, dict] = {}
    for variant in VARIANTS:
        info = fetch_json(
            f"https://huggingface.co/api/models/{REPO_ID}/revision/{variant}?blobs=true"
        )
        commit = info["sha"]
        files: dict[str, dict] = {}
        for sibling in info.get("siblings", []):
            name = sibling.get("rfilename")
            if name in (".gitattributes", "README.md"):
                continue
            lfs = sibling.get("lfs") or {}
            sha256 = lfs.get("sha256")
            size = lfs.get("size") or sibling.get("size")
            if not sha256:
                raw = fetch_bytes(
                    f"https://huggingface.co/{REPO_ID}/raw/{commit}/{urllib.parse.quote(name)}"
                )
                sha256 = hashlib.sha256(raw).hexdigest()
                size = len(raw)
            files[name] = {"sha256": sha256, "size": size}
            print(f"{variant:9s} {name:22s} {sha256[:16]}... {size}")
        manifest[variant] = {
            "repo": REPO_ID,
            "commit": commit,
            "files": files,
        }
        print(f"{variant}: commit {commit}")

    payload = {
        "_comment": "Сгенерировано tools/gen_model_manifest.py. Не редактировать вручную.",
        "schema": 1,
        "repo": REPO_ID,
        "variants": manifest,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(len(v["files"]) for v in manifest.values())
    print(f"\nГотово: {OUT} ({len(manifest)} ревизий, {total} записей)")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: PLC0415

    sys.exit(main())
