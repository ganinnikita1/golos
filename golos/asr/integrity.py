"""Проверка целостности модели GigaAM-v3.

Зачем это нужно. Приложение исполняет код модели (``modeling_gigaam.py``) и грузит
веса. Если репозиторий модели на Hugging Face скомпрометируют или подменят ветку
ревизии, пользователь может выполнить чужой код. Поэтому:

* ревизия фиксируется на неизменяемом commit SHA (``model_manifest.json``);
* SHA256 каждого файла модели сверяется с манифестом до импорта remote-кода;
* при несовпадении загрузка модели прерывается — код не исполняется.

Манифест генерируется ``tools/gen_model_manifest.py`` из официальных метаданных
Hugging Face (для LFS-файлов хеш берётся из метаданных HF, для остальных — считается
по raw-файлу на зафиксированном коммите).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")

#: Размер блока чтения при подсчёте SHA256.
_CHUNK = 1024 * 1024

#: Возможные имена файла весов.
_WEIGHT_NAMES = ("pytorch_model.bin", "model.safetensors")


@dataclass(frozen=True)
class FileExpectation:
    sha256: str
    size: int


@dataclass(frozen=True)
class VariantExpectation:
    repo: str
    variant: str
    commit: str
    files: dict[str, FileExpectation]


def _read_manifest() -> dict | None:
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Не удалось прочитать манифест целостности модели: %s", MANIFEST_PATH)
        return None
    return data if isinstance(data, dict) else None


_MANIFEST: dict | None = None
_MANIFEST_LOADED = False


def manifest() -> dict | None:
    global _MANIFEST, _MANIFEST_LOADED
    if not _MANIFEST_LOADED:
        _MANIFEST = _read_manifest()
        _MANIFEST_LOADED = True
    return _MANIFEST


def variant_expectation(repo: str, variant: str) -> VariantExpectation | None:
    """Ожидаемые commit/хеши для репозитория и ревизии; None — если их нет."""
    data = manifest()
    if not data or data.get("repo") != repo:
        return None
    variants = data.get("variants") or {}
    entry = variants.get(variant)
    if not isinstance(entry, dict):
        return None
    files: dict[str, FileExpectation] = {}
    for name, info in (entry.get("files") or {}).items():
        if not isinstance(info, dict) or not info.get("sha256"):
            continue
        files[name] = FileExpectation(
            sha256=str(info["sha256"]).lower(),
            size=int(info.get("size") or 0),
        )
    commit = str(entry.get("commit") or "")
    if not commit or not files:
        return None
    return VariantExpectation(repo=repo, variant=variant, commit=commit, files=files)


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_dir(
    model_dir: Path | str,
    repo: str,
    variant: str,
) -> list[str]:
    """Сверяет файлы модели с манифестом.

    Возвращает список проблем (пустой — всё совпало). Отсутствие манифеста для
    репозитория/ревизии не является ошибкой: это пользовательская модель, для неё
    проверка просто не применяется (caller решает, предупредить или нет).
    """
    expectation = variant_expectation(repo, variant)
    if expectation is None:
        return []
    root = Path(model_dir)
    problems: list[str] = []
    for name, expected in expectation.files.items():
        # Файл весов может называться по-разному — проверяем все варианты.
        candidates = [root / name]
        if name in _WEIGHT_NAMES:
            candidates = [root / candidate for candidate in _WEIGHT_NAMES]
        existing = [candidate for candidate in candidates if candidate.is_file()]
        if not existing:
            problems.append(f"отсутствует файл {name}")
            continue
        target = existing[0]
        if expected.size and target.stat().st_size != expected.size:
            problems.append(
                f"размер {target.name} не совпадает: ожидалось {expected.size} байт, "
                f"получено {target.stat().st_size}"
            )
            continue
        actual = sha256_file(target)
        if actual != expected.sha256:
            problems.append(
                f"SHA256 {target.name} не совпадает с проверенным "
                f"(ожидалось {expected.sha256[:16]}…, получено {actual[:16]}…)"
            )
    return problems


def describe_expectation(repo: str, variant: str) -> str:
    expectation = variant_expectation(repo, variant)
    if expectation is None:
        return "проверенных хешей нет"
    return f"commit {expectation.commit[:12]}, файлов в манифесте: {len(expectation.files)}"
