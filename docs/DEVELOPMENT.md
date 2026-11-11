# Разработка «Голоса»

Практическое руководство: как поднять окружение, запускать, тестировать и собирать
приложение. Архитектура — в [ARCHITECTURE.md](ARCHITECTURE.md).

## Требования

* Windows 10/11 x64;
* Python 3.13 (проект проверяется на 3.13, минимум — 3.11);
* ~5 ГБ свободного места (venv с CPU-сборкой torch ≈4 ГБ, модель ≈450 МБ);
* для сборки exe — PyInstaller (ставится в `tools/bootstrap_env.py`).

GPU не нужен: всё работает на CPU (проверено на AMD Ryzen 7 8845HS без CUDA).

## Окружение

```powershell
cd golos
python -m venv .venv
python tools\bootstrap_env.py
```

`bootstrap_env.py` ставит:

* `torch==2.8.0+cpu`, `torchaudio==2.8.0+cpu` с индекса PyTorch CPU;
* `transformers==4.57.1`, `sentencepiece`, `huggingface_hub`, `hydra-core`, `omegaconf`
  (нужны remote-коду GigaAM);
* `sounddevice`, `soundfile`, `numpy`, `PySide6`, `Pillow`, `faster-whisper`, `psutil`;
* `pytest`, `pyinstaller`.

Запуск из исходников:

```powershell
.\run.ps1                 # pythonw, трей
.\run.ps1 -Log            # консоль + живые логи
.\run.ps1 -Cli selftest   # проверка окружения (микрофоны, модель, вставка, хоткей)
.\run.ps1 -Cli devices    # список микрофонов
.\run.ps1 -Cli transcribe tests\fixtures\ru_tts\01.wav
```

## Если python не может писать в %APPDATA%

В ограниченных окружениях (песочницы, урезанные профили) запись за пределы рабочей
папки может быть запрещена. Используйте `dev_env.py` — он перенаправляет данные
приложения в `.devdata/` и кэш HuggingFace туда же:

```powershell
.\.venv\Scripts\python.exe dev_env.py -- .\.venv\Scripts\python.exe -m golos.cli selftest
.\.venv\Scripts\python.exe dev_env.py -- .\.venv\Scripts\python.exe -m golos.ui.demo
```

Внутри тестов это делается автоматически в `tests/conftest.py`.

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q            # быстрые (без модели) — то же, что в CI
.\.venv\Scripts\python.exe -m pytest tests -m slow       # реальная модель / сквозные
.\.venv\Scripts\python.exe -m pytest tests -m gui        # GUI-тесты
.\.venv\Scripts\python.exe -m pytest tests -q -k inject  # по подстроке
```

Маркеры: `slow` (нужна модель/долго), `gui` (нужен дисплей), `win32` (реальные
Win32-вызовы). В CI запускаются только быстрые тесты (`-m "not slow"`), GUI — с
`QT_QPA_PLATFORM=offscreen`.

Тестовые аудио: `tests/fixtures/ru_tts/` (30 фраз, синтез Windows SAPI «Irina»).
Пересоздать набор: `powershell -File tests\fixtures\make_tts.ps1` (скрипт должен
запускаться вне песочницы: SAPI недоступен из урезанного токена).

Сквозной тест вставки:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_e2e_dictation.py -q -m "slow and gui"
```

Он поднимает окно-приёмник отдельным процессом (`tests/target_window.py`), прогоняет
реальную модель и проверяет, что текст появился в поле, буфер восстановлен, а RTF
укладывается в бюджет. В интерактивной сессии фокус иногда «уезжает» — тест делает
до трёх попыток; окно-приёмник само форсирует foreground-режим.

## Сборка и установка

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1     # dist\Golos\Golos.exe
powershell -ExecutionPolicy Bypass -File packaging\install.ps1 -DesktopShortcut
powershell -ExecutionPolicy Bypass -File packaging\install.ps1 -Uninstall
```

Спецификация PyInstaller: `packaging/golos.spec` (one-dir, без консоли). Ярлыки
создаются через `cscript` (`packaging/make_shortcut.vbs`), потому что PowerShell в
ограниченных сессиях не имеет доступа к COM.

Диагностика собранного приложения:

```powershell
"%LOCALAPPDATA%\Programs\Golos\Golos.exe" --diagnose   # пути, логи, состояние модели
```

## Инструменты

| Скрипт | Назначение |
|---|---|
| `tools/bootstrap_env.py` | установка зависимостей в `.venv` |
| `tools/pip_install.py` | pip с очищенным прокси-окружением и локальным кэшем |
| `tools/eval_ru.py` | бенчмарк WER/CER/RTF по вариантам модели, `--from-report`, `--whisper` |
| `tools/gen_model_manifest.py` | пересоздать манифест целостности модели (SHA256 + commit SHA) |
| `tools/diagnose_paths.py` | отчёт о путях/логировании для поддержки |
| `tools/smoke_exe.py` | smoke-тест собранного exe (запуск, модель, хоткей) |
| `tools/hotkey_probe.py` | проверка доставки горячей клавиши в реальное окно |
| `tools/launch_detached.py` | запуск GUI-приложения отсоединённым процессом |
| `dev_env.py` | перенаправление данных приложения в `.devdata/` |
| `requirements/` | закреплённые версии зависимостей и как получить lock с хешами |

## Стиль кода

* Python 3.13, `from __future__ import annotations`, type hints на публичных функциях.
* Строки — до 100 символов, импорты стандартные/сторонние/локальные.
* Русские тексты в UI и сообщениях об ошибках; имена в коде — английские.
* Никаких `print()` в коде приложения (только `logging`).
* Одна ответственность на модуль; Win32-вызовы — в `_win32.py`-подобных файлах.
* Изменения в публичных контрактах (`golos/controller.py`, `asr/base.py`,
  `text/base.py`, `inject/base.py`, `hotkey/base.py`) должны сопровождаться
  обновлением [ARCHITECTURE.md](ARCHITECTURE.md).

## Коммиты и PR

Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
Перед PR: `pytest tests -q` зелёный, CHANGELOG обновлён для пользовательских
изменений. Подробнее — [CONTRIBUTING.md](../CONTRIBUTING.md).
