# Архитектура «Голоса»

Документ описывает, как устроено приложение изнутри: слои, потоки, контракты модулей
и принятые решения. Его стоит прочитать перед изменением ядра (`golos/app.py`,
`golos/pipeline.py`) или добавлением нового движка распознавания.

## Общая картина

```
┌──────────────────────────────────────────────────────────────────────┐
│ Qt main thread: GolosUI (трей, настройки, оверлей, история)          │
│   ▲ QTimer 30 мс: bus.drain()                                        │
│   │ Controller API (get_config / start_recording / apply_config ...) │
├───┴──────────────────────────────────────────────────────────────────┤
│ DictationApp (golos/app.py) — жизненный цикл, единый экземпляр       │
│   • EventBus (потокобезопасная очередь событий)                      │
│   • Recorder (sounddevice/PortAudio)                                 │
│   • HotkeyListener (WH_KEYBOARD_LL, отдельный поток)                 │
│   • EngineManager (ленивая загрузка ASR-движка)                      │
│   • worker-поток: DictationPipeline.run(audio)                       │
└──────────────────────────────────────────────────────────────────────┘
```

Потоки и правила:

| Поток | Что делает | Правила |
|---|---|---|
| Qt main | интерфейс | только он трогает виджеты; никаких долгих вызовов |
| dictation-worker | `pipeline.run()`: ASR → текст → полировка → вставка | один вызов за раз, очередь `queue.Queue` |
| hotkey-hook | низкоуровневый хук клавиатуры + message loop | колбэк обязан отвечать за микросекунды; тяжёлая работа — в диспетчере действий |
| engine-loader | загрузка/скачивание модели | прогресс уходит в `EventBus` |
| PortAudio callback | приём аудиоблоков | только буферизация, уровень сигнала и учёт длительности |

## Пайплайн диктовки

`golos/pipeline.py::DictationPipeline.run(audio, sample_rate)`:

1. **Подготовка аудио** (`audio/utils.py`): моно, ресемплинг в 16 кГц, VAD-обрезка
   тишины по краям, нормализация громкости. Слишком короткая запись отбрасывается.
2. **Распознавание** — движок из `golos/asr/registry.py`. Если модель ещё не загружена,
   `load()` вызывается здесь (прогресс виден в оверлее), затем `transcribe()`.
3. **Постобработка** (`golos/text/postprocess.py`): пунктуация/регистр, словарь замен,
   удаление слов-паразитов, произносимые знаки, пробелы. Быстро (<5 мс на 1000 символов).
4. **LLM-полировка** (`golos/text/llm.py`, опция): OpenAI-совместимый chat endpoint.
   Любая ошибка/таймаут — возврат исходного текста.
5. **Вставка** (`golos/inject/`): прямой Unicode-ввод (по умолчанию) или буфер обмена.
6. **История** (`golos/history.py`): JSONL, лимит записей, атомарная перезапись.

Каждый шаг сообщает о себе в `EventBus`: `state`, `level`, `partial`, `result`,
`error`, `progress`, `toast`. UI читает события в главном потоке.

## Контракты модулей

Чтобы слои оставались заменяемыми, используются протоколы из `golos/controller.py`,
`golos/asr/base.py`, `golos/text/base.py`, `golos/inject/base.py`,
`golos/hotkey/base.py`. Ключевые контракты:

```python
# asr/base.py
class ASREngine(Protocol):
    name: str
    def load(self, progress: ProgressFn | None = None) -> None: ...
    def unload(self) -> None: ...
    def is_loaded(self) -> bool: ...
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript: ...

# text/base.py
def postprocess(text: str, cfg: TextConfig) -> str: ...
class Polisher(Protocol):
    def is_configured(self) -> bool: ...
    def polish(self, text: str, *, context: str | None = None) -> str: ...

# inject/base.py
class TextInjector(Protocol):
    def inject(self, text: str, cfg: InsertConfig) -> bool: ...

# hotkey/base.py
def create_listener(cfg: HotkeyConfig, *, on_start, on_stop,
                    on_cancel=None, state_getter=None) -> HotkeyListener: ...
```

Важные соглашения:

* тяжёлые зависимости (`torch`, `transformers`, `ctranslate2`) импортируются **лениво**
  внутри `load()` — UI должен стартовать за <1 с;
* движок обязан быть идемпотентным: повторный `transcribe()` не перезагружает модель;
* инжектор никогда не бросает исключений наружу, только `False`;
* `postprocess` и `polish` не должны падать на пустом тексте;
* логирование — только `logging`, без `print()`.

## ASR-слой

### GigaAM-v3 (по умолчанию)

* Репозиторий `ai-sage/GigaAM-v3` (MIT), вариант `e2e_rnnt` — текст сразу с пунктуацией
  и нормализованными числами.
* Веса и код модели скачиваются в `%LOCALAPPDATA%\Golos\models\GigaAM-v3` и
  проверяются маркером `.golos_model.json`.
* Remote-код грузится **напрямую** (`importlib` + `hydra`/`omegaconf` + `state_dict`),
  без валидатора `transformers` — это убирает ложную зависимость от `pyannote`
  и лишние импорты.
* Нет `ffmpeg` и временных WAV: используется `GigaAMASR.forward(waveform, lengths)`,
  аудио приходит как `float32` 16 кГц.
* Длинные записи режутся на чанки ≤20 с с перекрытием 0.5 с, текст склеивается
  без дублей.
* Потоки `torch` = физические ядра (по умолчанию), переопределяется `asr.torch_threads`.

### Целостность модели

`golos/asr/integrity.py` + `golos/asr/model_manifest.json`:

* для каждой стандартной ревизии зафиксирован неизменяемый commit SHA и SHA256
  всех файлов (для LFS-файлов хеш взят из официальных метаданных Hugging Face);
* `GigaAMEngine.load()` сначала скачивает модель (если нужно) **с этого коммита**,
  затем сверяет хеши и только после этого импортирует `modeling_gigaam.py`;
* несовпадение → `RuntimeError`, remote-код не исполняется;
* модели из неизвестных репозиториев/ревизий запрещены, пока пользователь явно не
  разрешит `asr.allow_unverified_model` (осознанное изменение границы доверия);
* манифест пересоздаётся `tools/gen_model_manifest.py` (нужен интернет).

Это защищает от сценария «скомпрометированный HF-репозиторий исполняет произвольный
код на машине пользователя».

### faster-whisper (опция)

`golos/asr/whisper_engine.py`: CTranslate2, CPU int8, ленивая загрузка, настройки
модели/compute_type/beam_size в конфиге. По бенчмарку — в ~14 раз медленнее GigaAM
и теряет финальную пунктуацию, поэтому это запасной движок для других языков.

### Облачный ASR (опция)

`golos/asr/cloud_engine.py`: OpenAI-совместимый `POST /audio/transcriptions`,
ключ только из переменной окружения (`asr.cloud_api_key_env`), понятные ошибки
при отсутствии ключа. По умолчанию выключен.

## Вставка текста и приватность

`golos/inject/injector.py`:

* **TypeInjector** (по умолчанию) — `SendInput(KEYEVENTF_UNICODE)` чанками,
  поддержка `\n`/`\t`, суррогатных пар. Буфер обмена не трогается вообще.
* **ClipboardInjector** — снимок всех форматов буфера, `SetClipboardData`,
  `Ctrl+V`, возврат снимка. Дополнительно:
  * служебные форматы `CanIncludeInClipboardHistory=0`,
    `CanUploadToCloudClipboard=0`, `ExcludeClipboardContentFromMonitorProcessing`
    исключают текст из журнала Win+V и облака;
  * перед восстановлением проверяется, что в буфере всё ещё наш текст (не затираем
    чужие данные);
  * если снимок вернуть не удалось — буфер очищается, чтобы диктовка не осталась.

Все Win32-вызовы изолированы в `golos/inject/_win32.py` (ctypes, без сторонних пакетов).

## Глобальная клавиша

`golos/hotkey/hook.py`: `WH_KEYBOARD_LL` в отдельном потоке с `GetMessage`-циклом.
Колбэк только обновляет состояние и кладёт действия в очередь, пользовательские
колбэки выполняет отдельный диспетчер. Режимы `toggle`/`push_to_talk`, `Esc`-отмена
только во время записи (`state_getter`), подавление клавиш комбинации, чтобы они не
просачивались в активное приложение. Прав администратора не требуется.

## Конфигурация и файлы

* `golos/config.py` — dataclass-схема, дефолты, мягкая валидация, атомарная запись,
  чтение с `utf-8-sig` (BOM не ломает настройки).
* `golos/paths.py` — выбор каталогов: env-override (`GOLOS_APPDATA`,
  `GOLOS_LOCALAPPDATA`) → `%APPDATA%`/`%LOCALAPPDATA%` → `%USERPROFILE%\Golos` →
  `_data` рядом с exe. Каталог выбирается один раз и кэшируется.
* `golos/logging_setup.py` — ротируемый лог в `paths.logs_dir()`, консоль только в
  dev-режиме.
* `%APPDATA%\Golos\config.json` версионируется полем `version`.

## Тестирование

* `tests/` — 300+ тестов; тяжёлые помечены `slow`, GUI-тесты — `gui`.
* Быстрые тесты не требуют модели и прав администратора: конфиг, аудио-утилиты,
  пайплайн на фейковом движке, постобработка, парсер хоткеев, логика вставки.
* Тест на реальной модели (`tests/test_asr_integration.py`) и сквозной
  `tests/test_e2e_dictation.py` (реальная модель → вставка в отдельное окно) —
  маркер `slow`; запускаются явно.
* Тестовые WAV генерируются `tests/fixtures/make_tts.ps1` (Windows SAPI «Irina»),
  готовый набор лежит в `tests/fixtures/ru_tts/`.
