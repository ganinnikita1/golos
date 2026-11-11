"""Движок GigaAM-v3 (ai-sage/GigaAM-v3, MIT) на CPU через transformers remote code.

Почему не ``model.transcribe(path)``: официальный remote-код читает файл через
ffmpeg-подпроцесс (``load_audio``), а нам нужен numpy-массив из микрофона.
Поэтому вызываем внутренний пайплайн напрямую:

    model.model.forward(waveform_16k, lengths)  -> encoded, encoded_len
    model.model.decoding.decode(head, encoded, encoded_len) -> [text]

``forward`` принимает сырой waveform (B, T) на 16 кГц: мел-спектрограмма
считается внутри (``FeatureExtractor``). Так мы не пишем временных WAV и не
зависим от ffmpeg. Декодер (CTC или R-NNNT) выбирается конфигом ревизии.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .. import paths
from ..config import ASRConfig
from .audio_utils import DEFAULT_SR, chunk_audio, merge_texts, resample, to_mono_float32
from .base import ProgressFn, Transcript

logger = logging.getLogger(__name__)

#: Маркер успешной загрузки: помогает не перекачивать 450 МБ каждый запуск.
_MARKER_NAME = ".golos_model.json"

#: Файлы, без которых моделью нельзя воспользоваться.
_REQUIRED_FILES = ("config.json", "modeling_gigaam.py", "tokenizer.model")

#: Перекрытие чанков (сек): модель любит чуть больше контекста на стыках.
_CHUNK_OVERLAP_S = 0.5


def model_dirname(cfg: ASRConfig) -> str:
    """Каталог модели внутри models_dir.

    Для варианта по умолчанию (e2e_rnnt) это ровно ``GigaAM-v3`` — как
    заявлено в конфиге (``cfg.gigaam_dirname``). Остальные ревизии кладём
    рядом в отдельный каталог, чтобы бенчмарк мог держать их все сразу.
    """
    if cfg.gigaam_variant == "e2e_rnnt":
        return cfg.gigaam_dirname
    return f"{cfg.gigaam_dirname}-{cfg.gigaam_variant}"


def gigaam_model_dir(cfg: ASRConfig, models_dir: Path | str | None = None) -> Path:
    root = Path(models_dir) if models_dir is not None else paths.models_dir()
    return root / model_dirname(cfg)


def _read_marker(model_dir: Path) -> dict | None:
    try:
        return json.loads((model_dir / _MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _weight_file(model_dir: Path) -> Path | None:
    for pattern in ("pytorch_model.bin", "model.safetensors", "model*.safetensors"):
        hits = sorted(model_dir.glob(pattern))
        if hits:
            return hits[0]
    return None


def gigaam_model_installed(
    cfg: ASRConfig, models_dir: Path | str | None = None
) -> bool:
    """Есть ли модель на диске и совпадает ли она с запрошенной ревизией."""
    model_dir = gigaam_model_dir(cfg, models_dir)
    if not model_dir.is_dir():
        return False
    for name in _REQUIRED_FILES:
        if not (model_dir / name).is_file():
            return False
    if _weight_file(model_dir) is None:
        return False
    marker = _read_marker(model_dir)
    if marker:
        return (
            marker.get("repo") == cfg.gigaam_repo
            and marker.get("revision") == cfg.gigaam_variant
        )
    # Маркера нет (модель скопирована руками) — проверяем по имени в config.json.
    try:
        data = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    model_name = (
        data.get("cfg", {}).get("model", {}).get("cfg", {}).get("model_name", "") or ""
    )
    return str(cfg.gigaam_variant) in str(model_name)


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _human_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024.0 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} ГБ"


def _configure_torch_threads(torch_module, cfg: ASRConfig) -> int:
    """Выставляет разумное число intra-op потоков для CPU-инференса.

    По умолчанию берём физические ядра (у Ryzen 7 8845HS их 8): hyper-threading
    для конформера чаще мешает. Пользователь может переопределить
    ``cfg.torch_threads``.
    """
    requested = int(getattr(cfg, "torch_threads", 0) or 0)
    if requested > 0:
        threads = requested
    else:
        physical = None
        try:
            import psutil

            physical = psutil.cpu_count(logical=False)
        except Exception:
            physical = None
        if not physical:
            physical = max(1, (os.cpu_count() or 8) // 2)
        threads = min(16, max(1, int(physical)))
    try:
        torch_module.set_num_threads(threads)
    except Exception:
        logger.warning("Не удалось выставить torch.set_num_threads(%s)", threads)
    try:
        torch_module.set_num_interop_threads(1)
    except Exception:
        # Уже инициализировано другим кодом — не критично.
        pass
    logger.info("GigaAM: torch intra-op threads = %s", threads)
    return threads


class GigaAMEngine:
    """CPU-движок GigaAM-v3 с пунктуацией (варианты e2e_ctc / e2e_rnnt)."""

    name = "gigaam"

    def __init__(self, cfg: ASRConfig, models_dir: Path | str | None = None) -> None:
        self.cfg = cfg
        self._models_dir = Path(models_dir) if models_dir is not None else paths.models_dir()
        self._model = None
        self._asr = None
        self._torch = None
        self._device = None
        self._dtype = None
        self._threads: int = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # свойства
    # ------------------------------------------------------------------ #
    @property
    def model_dir(self) -> Path:
        return gigaam_model_dir(self.cfg, self._models_dir)

    def is_loaded(self) -> bool:
        return self._model is not None

    # ------------------------------------------------------------------ #
    # загрузка
    # ------------------------------------------------------------------ #
    def load(self, progress: ProgressFn | None = None) -> None:
        with self._lock:
            if self._model is not None:
                return
            self._configure_hf_cache()
            self._report(progress, "Инициализация torch…", 0.05)
            import torch

            self._torch = torch
            self._threads = _configure_torch_threads(torch, self.cfg)

            # Не качаем сотни мегабайт, если всё равно не разрешим непроверенную модель.
            if bool(getattr(self.cfg, "verify_model_hashes", True)) and not bool(
                getattr(self.cfg, "allow_unverified_model", False)
            ):
                from .integrity import variant_expectation

                if variant_expectation(self.cfg.gigaam_repo, self.cfg.gigaam_variant) is None:
                    raise RuntimeError(
                        "Для этой модели нет проверенных хешей, а исполнять непроверенный "
                        f"remote-код запрещено: {self.cfg.gigaam_repo}/"
                        f"{self.cfg.gigaam_variant}. Выберите стандартный вариант модели "
                        "или разрешите непроверенные модели в настройках "
                        "(asr.allow_unverified_model)."
                    )

            if not gigaam_model_installed(self.cfg, self._models_dir):
                self._download(progress)

            self._verify_integrity(progress)
            self._report(progress, "Загрузка GigaAM-v3 в память…", 0.75)
            self._load_model(progress)
            self._report(progress, "Модель GigaAM-v3 готова", 1.0)

    def _configure_hf_cache(self) -> None:
        """Уводит кэш huggingface внутрь данных приложения.

        ``snapshot_download`` пишет блобы в HF_HOME/hub, а ``transformers`` —
        скачанный remote code в HF_MODULES_CACHE. На портативной/офлайн-установке
        домашний ``~/.cache`` может быть недоступен на запись, поэтому по
        умолчанию используем ``paths.cache_dir()/huggingface``.
        """
        if os.environ.get("HF_HOME"):
            return
        try:
            cache = paths.cache_dir() / "huggingface"
            cache.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("HF_HOME", str(cache))
            logger.debug("HF_HOME=%s", cache)
        except OSError:
            logger.debug("Не удалось создать кэш huggingface", exc_info=True)

    # ------------------------------------------------------------------ #
    # целостность модели
    # ------------------------------------------------------------------ #
    def _verify_integrity(self, progress: ProgressFn | None) -> None:
        """Сверяет файлы модели с проверенным манифестом ДО исполнения remote-кода.

        Это защита от подмены модели на Hugging Face: даже если ветку ревизии
        перепишут, хеши файлов не совпадут и приложение откажется грузить модель
        (а значит, не выполнит чужой ``modeling_gigaam.py``).
        """
        if not bool(getattr(self.cfg, "verify_model_hashes", True)):
            logger.info("Проверка целостности модели отключена в настройках")
            return
        from .integrity import describe_expectation, variant_expectation, verify_model_dir

        expectation = variant_expectation(self.cfg.gigaam_repo, self.cfg.gigaam_variant)
        if expectation is None:
            if bool(getattr(self.cfg, "allow_unverified_model", False)):
                logger.warning(
                    "Для %s/%s нет проверенных хешей — проверка целостности пропущена "
                    "(разрешено в настройках)",
                    self.cfg.gigaam_repo,
                    self.cfg.gigaam_variant,
                )
                return
            raise RuntimeError(
                "Для этой модели нет проверенных хешей, а исполнять непроверенный "
                f"remote-код запрещено: {self.cfg.gigaam_repo}/{self.cfg.gigaam_variant}. "
                "Выберите стандартный вариант модели или разрешите непроверенные "
                "модели в настройках (asr.allow_unverified_model)."
            )
        self._report(progress, "Проверка целостности модели…", 0.72)
        problems = verify_model_dir(self.model_dir, self.cfg.gigaam_repo, self.cfg.gigaam_variant)
        if problems:
            details = "; ".join(problems[:3])
            raise RuntimeError(
                "Файлы модели GigaAM-v3 не совпадают с проверенными (возможна подмена "
                f"или повреждение): {details}. Модель не загружена. Удалите каталог "
                f"модели и скачайте заново; если это повторяется — не используйте "
                f"источник. Подробнее: {describe_expectation(self.cfg.gigaam_repo, self.cfg.gigaam_variant)}"
            )
        logger.info(
            "Целостность GigaAM-v3 подтверждена (commit %s, файлов: %s)",
            expectation.commit[:12],
            len(expectation.files),
        )

    # ------------------------------------------------------------------ #
    # загрузка весов
    # ------------------------------------------------------------------ #
    def _load_model(self, progress: ProgressFn | None) -> None:
        """Загружает GigaAM-v3 из локального каталога.

        Основной путь — импортировать ``modeling_gigaam.py`` как обычный модуль
        и собрать ``GigaAMASR`` через hydra/omegaconf из ``config.json``.
        Почему не ``AutoModel.from_pretrained(trust_remote_code=True)``:
        валидатор transformers сканирует все импорты remote-файла и требует
        пакет ``pyannote`` — он нужен только для необязательного
        ``transcribe_longform`` (VAD), который мы не используем. Тащить в
        приложение тяжёлый pyannote ради валидатора нельзя, поэтому грузим тот же
        remote-код напрямую. На случай изменения формата оставлен fallback через
        transformers (с понятной ошибкой, если он тоже не сможет).
        """
        model_dir = self.model_dir
        try:
            asr = self._load_remote_direct(model_dir)
            self._model = asr
            self._asr = asr
            logger.info("GigaAM-v3 '%s': remote-код загружен напрямую", self.cfg.gigaam_variant)
        except Exception as direct_error:  # noqa: BLE001 - покажем fallback
            logger.warning(
                "Прямая загрузка GigaAM-v3 не удалась (%s), пробую через transformers",
                direct_error,
            )
            self._load_via_transformers(model_dir, direct_error)

        asr = self._asr
        if asr is None or not hasattr(asr, "forward"):
            raise RuntimeError(
                "Неожиданный формат модели GigaAM-v3: нет ASR-модуля. "
                "Проверьте ревизию gigaam_variant."
            )
        try:
            asr.eval()
            asr.requires_grad_(False)
        except Exception:  # pragma: no cover - страховка
            logger.debug("Не удалось перевести модель в eval/requires_grad(False)", exc_info=True)
        try:
            param = next(asr.parameters())
            self._device = param.device
            self._dtype = param.dtype
        except StopIteration:  # pragma: no cover
            self._device = self._torch.device("cpu")
            self._dtype = self._torch.float32

        logger.info(
            "GigaAM-v3 '%s' загружена (%s, %s), threads=%s",
            self.cfg.gigaam_variant,
            self._device,
            self._dtype,
            self._threads,
        )

    def _load_remote_direct(self, model_dir: Path):
        """Импортирует remote-код и собирает GigaAMASR с весами из локальной папки."""
        import importlib.util

        source = model_dir / "modeling_gigaam.py"
        if not source.is_file():
            raise RuntimeError(f"Нет файла remote-кода: {source}")

        spec = importlib.util.spec_from_file_location("modeling_gigaam", str(source))
        if spec is None or spec.loader is None:  # pragma: no cover
            raise RuntimeError(f"Не удалось импортировать remote-код: {source}")
        module = importlib.util.module_from_spec(spec)
        # hydra резолвит _target_ вида "modeling_gigaam.GigaAMASR" по sys.modules
        sys.modules["modeling_gigaam"] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop("modeling_gigaam", None)
            raise

        try:
            import omegaconf
            from hydra.utils import instantiate
        except ImportError as exc:
            raise RuntimeError(
                "Для GigaAM-v3 не хватает пакетов hydra-core / omegaconf. "
                "Запустите tools/bootstrap_env.py."
            ) from exc

        try:
            config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
            model_cfg = config["cfg"]["model"]
        except (OSError, ValueError, KeyError) as exc:
            raise RuntimeError(f"Битый config.json модели GigaAM-v3: {exc}") from exc

        inner = model_cfg.get("cfg", {}) or {}
        decoding = inner.get("decoding") or {}
        if decoding.get("model_path"):
            decoding["model_path"] = str((model_dir / decoding["model_path"]).resolve())

        asr = instantiate(omegaconf.OmegaConf.create(model_cfg), _recursive_=False)
        self._load_weights(asr, model_dir, inner.get("model_name"))
        return asr

    def _load_weights(self, asr, model_dir: Path, model_name: str | None = None) -> None:
        """Читает state_dict (bin/safetensors) и аккуратно загружает в модель."""
        torch = self._torch
        state: dict | None = None
        bin_path = model_dir / "pytorch_model.bin"
        if bin_path.is_file():
            state = torch.load(bin_path, map_location="cpu", weights_only=True)
        if state is None:
            for pattern in ("model*.safetensors", "*.safetensors"):
                hits = sorted(model_dir.glob(pattern))
                if not hits:
                    continue
                try:
                    from safetensors.torch import load_file
                except ImportError as exc:  # pragma: no cover
                    raise RuntimeError(
                        "Не установлен safetensors: запустите tools/bootstrap_env.py"
                    ) from exc
                state = load_file(str(hits[0]))
                break
        if state is None:
            raise RuntimeError(
                f"В {model_dir} нет весов модели (pytorch_model.bin / *.safetensors)"
            )

        # У чекпойнта GigaAMModel веса лежат с префиксом "model.", у самого
        # GigaAMASR его нет — снимаем префикс, если он есть у всех ключей.
        prefix = "model."
        if state and all(key.startswith(prefix) for key in state):
            state = {key[len(prefix):]: value for key, value in state.items()}
        missing, unexpected = asr.load_state_dict(state, strict=False)
        if missing:
            raise RuntimeError(
                f"Не хватает весов модели {model_name or model_dir.name}: "
                f"{list(missing)[:5]} (всего {len(missing)})"
            )
        if unexpected:
            logger.warning(
                "Лишние ключи в чекпойнте %s: %s (всего %d)",
                model_name or model_dir.name,
                list(unexpected)[:5],
                len(unexpected),
            )

    def _load_via_transformers(self, model_dir: Path, direct_error: Exception) -> None:
        """Резервный путь: AutoModel + trust_remote_code."""
        try:
            from transformers import AutoModel
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Не установлен transformers: запустите tools/bootstrap_env.py"
            ) from direct_error

        self._allow_optional_remote_imports()
        try:
            model = AutoModel.from_pretrained(str(model_dir), trust_remote_code=True)
        except ImportError as exc:
            raise RuntimeError(
                "Не удалось загрузить GigaAM-v3. Прямой путь: "
                f"{direct_error}. remote-код transformers требует пакеты: {exc}"
            ) from direct_error
        except Exception as exc:
            raise RuntimeError(
                f"Не удалось загрузить GigaAM-v3 из {model_dir}: {exc} "
                f"(прямой путь: {direct_error})"
            ) from direct_error

        asr = getattr(model, "model", None)
        if asr is None:
            raise RuntimeError(
                "transformers вернул модель без внутреннего ASR-модуля"
            ) from direct_error
        self._model = model
        self._asr = asr

    @staticmethod
    def _allow_optional_remote_imports() -> None:
        """Ослабляет строгую проверку импортов remote-кода в transformers.

        ``transformers`` сканирует ВСЕ импорты файла модели, включая ленивый
        ``from pyannote.audio import Model`` внутри необязательного
        longform-VAD (``transcribe_longform``/``get_pipeline``), которым мы не
        пользуемся (режем аудио сами). Без патча загрузка падает с требованием
        установить pyannote — тяжёлый пакет, не нужный для распознавания.
        Патчим только недостающие опциональные пакеты, остальные проверяем строго.
        """
        try:
            import importlib

            import transformers.dynamic_module_utils as dmu
        except Exception:  # pragma: no cover - transformers нет, fallback и так упадёт
            return
        if getattr(dmu, "_golos_lenient_imports", False):
            return

        optional_roots = {"pyannote"}
        original = getattr(dmu, "check_imports", None)

        def lenient_check_imports(filename):  # noqa: ANN001 - сигнатура transformers
            missing: list[str] = []
            try:
                imports = dmu.get_imports(filename)
            except Exception:  # pragma: no cover - парсер не справился
                imports = []
            for module_name in imports:
                try:
                    importlib.import_module(module_name)
                except ImportError as exc:
                    if module_name.split(".")[0] in optional_roots:
                        continue
                    if "No module named" in str(exc):
                        missing.append(module_name)
            if missing:
                raise ImportError(
                    "Для remote-кода GigaAM-v3 не хватает пакетов: "
                    + ", ".join(sorted(set(missing)))
                )
            return original(filename) if original is not None else []

        dmu.check_imports = lenient_check_imports
        dmu._golos_lenient_imports = True  # type: ignore[attr-defined]

    def _download(self, progress: ProgressFn | None) -> None:
        """Качает ревизию модели одним snapshot_download с прогрессом."""
        cfg = self.cfg
        model_dir = self.model_dir
        model_dir.mkdir(parents=True, exist_ok=True)
        try:
            from huggingface_hub import HfApi, snapshot_download
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Не установлен huggingface_hub: запустите tools/bootstrap_env.py"
            ) from exc

        total = 0
        # Качаем с неизменяемого commit SHA, если для ревизии есть проверенный манифест:
        # имя ветки (revision) на HF может измениться, SHA — нет.
        revision = cfg.gigaam_variant
        pinned_commit = ""
        try:
            from .integrity import variant_expectation

            expectation = variant_expectation(cfg.gigaam_repo, cfg.gigaam_variant)
            if expectation is not None and bool(getattr(cfg, "pin_model_revision", True)):
                revision = expectation.commit
                pinned_commit = expectation.commit
                logger.info(
                    "Скачиваю GigaAM-v3 с закреплённого коммита %s (ревизия %s)",
                    pinned_commit[:12],
                    cfg.gigaam_variant,
                )
        except Exception:  # noqa: BLE001 - не мешаем загрузке
            logger.debug("Не удалось получить закреплённый коммит модели", exc_info=True)

        try:
            api = HfApi()
            for item in api.list_repo_tree(
                cfg.gigaam_repo, revision=revision, recursive=True
            ):
                size = getattr(item, "size", None)
                if size:
                    total += int(size)
        except Exception:
            logger.debug("Не удалось получить полный размер репозитория", exc_info=True)

        label = f"{cfg.gigaam_repo} ({cfg.gigaam_variant})"
        self._report(
            progress,
            f"Скачивание модели {cfg.gigaam_dirname}…"
            + (f" {_human_size(total)}" if total else ""),
            0.1,
        )

        outcome: dict[str, object] = {}

        def _worker() -> None:
            try:
                outcome["path"] = snapshot_download(
                    repo_id=cfg.gigaam_repo,
                    revision=revision,
                    local_dir=str(model_dir),
                )
            except Exception as exc:  # noqa: BLE001 - прокидываем в основной поток
                outcome["error"] = exc

        worker = threading.Thread(target=_worker, name="gigaam-download", daemon=True)
        worker.start()
        last_report = 0.0
        while worker.is_alive():
            worker.join(timeout=0.5)
            now = time.monotonic()
            if now - last_report < 0.4:
                continue
            last_report = now
            current = _dir_size(model_dir)
            fraction = min(0.99, current / total) if total else None
            message = f"Скачивание модели {cfg.gigaam_dirname}: {_human_size(current)}"
            if total:
                message += f" из {_human_size(total)}"
            self._report(progress, message, fraction)

        error = outcome.get("error")
        if error is not None:
            raise RuntimeError(f"Не удалось скачать модель {label}: {error}") from error

        marker = {
            "repo": cfg.gigaam_repo,
            "revision": cfg.gigaam_variant,
            "commit": pinned_commit,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "size_bytes": _dir_size(model_dir),
        }
        try:
            (model_dir / _MARKER_NAME).write_text(
                json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.warning("Не удалось записать маркер модели в %s", model_dir)
        self._report(progress, "Модель скачана", 0.7)

    # ------------------------------------------------------------------ #
    # распознавание
    # ------------------------------------------------------------------ #
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        if self._model is None:
            raise RuntimeError("Модель GigaAM-v3 не загружена: вызовите load()")

        wav = resample(to_mono_float32(audio), int(sample_rate or DEFAULT_SR), DEFAULT_SR)
        duration = float(wav.size) / DEFAULT_SR
        started = time.perf_counter()
        if wav.size == 0:
            return Transcript(text="", engine=self.name, duration_s=0.0, elapsed_s=0.0, raw="")

        max_chunk = float(getattr(self.cfg, "max_chunk_s", 20.0) or 20.0)
        with self._torch.inference_mode():
            if duration <= max_chunk:
                text = self._transcribe_chunk(wav)
            else:
                chunks = chunk_audio(wav, DEFAULT_SR, max_chunk, _CHUNK_OVERLAP_S)
                parts = [self._transcribe_chunk(chunk) for chunk in chunks]
                text = merge_texts(parts)
        elapsed = time.perf_counter() - started
        text = (text or "").strip()
        logger.debug(
            "GigaAM: %.2f c аудио -> %.2f c (RTF %.3f), %d симв.",
            duration,
            elapsed,
            (elapsed / duration if duration else 0.0),
            len(text),
        )
        return Transcript(
            text=text,
            engine=self.name,
            language="ru",
            duration_s=duration,
            elapsed_s=elapsed,
            raw=text,
        )

    def _transcribe_chunk(self, wav: np.ndarray) -> str:
        torch = self._torch
        tensor = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32)).unsqueeze(0)
        tensor = tensor.to(device=self._device, dtype=self._dtype)
        lengths = torch.full((1,), int(tensor.shape[-1]), dtype=torch.long, device=self._device)
        encoded, encoded_len = self._asr.forward(tensor, lengths)
        return self._asr.decoding.decode(self._asr.head, encoded, encoded_len)[0]

    # ------------------------------------------------------------------ #
    # выгрузка
    # ------------------------------------------------------------------ #
    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._asr = None
            self._device = None
            self._dtype = None
            gc.collect()

    def __del__(self) -> None:  # pragma: no cover - защита от шумных исключений
        try:
            self.unload()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    @staticmethod
    def _report(progress: ProgressFn | None, message: str, fraction: float | None) -> None:
        if progress is None:
            return
        try:
            progress(message, fraction)
        except Exception:  # колбэк UI не должен ломать загрузку
            logger.debug("progress callback упал", exc_info=True)
