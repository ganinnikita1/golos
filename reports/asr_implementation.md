# ASR-слой «Голоса»: как устроено и почему так

Документ фиксирует решения по `golos/asr/*` и способы их проверки.
Бенчмарк и цифры — в [asr_bench.md](asr_bench.md).

## Состав

| Файл | Назначение |
|---|---|
| `golos/asr/base.py` | контракт `ASREngine` / `Transcript` (не менялся) |
| `golos/asr/audio_utils.py` | приведение к float32 mono, ресемплинг в 16 кГц, нарезка длинного аудио на чанки с перекрытием, склейка текста без дублей, WAV-кодирование для облака |
| `golos/asr/gigaam_engine.py` | основной офлайн-движок GigaAM-v3 на CPU (e2e_rnnt по умолчанию) |
| `golos/asr/whisper_engine.py` | опциональный faster-whisper (CTranslate2, int8) |
| `golos/asr/cloud_engine.py` | OpenAI-совместимый `POST /audio/transcriptions` |
| `golos/asr/registry.py` | `create_engine`, `engine_is_installed`, `download_model`, `engine_info` |
| `tools/eval_ru.py` | офлайн-бенчмарк WER/CER/RTF + генерация отчёта |
| `tests/test_asr_*.py` | юнит-тесты + интеграционный тест на реальной модели |

## Ключевые решения

### 1. Сырой waveform, без ffmpeg и без временных WAV
Официальный пример GigaAM использует `model.transcribe("example.wav")`, а внутри
remote-код вызывает **ffmpeg** через подпроцесс (`load_audio`). Пользователь не должен
ничего доустанавливать, поэтому движок идёт низкоуровневым путём:

```python
tensor = torch.from_numpy(wav_16k).unsqueeze(0)      # (1, N) float32
encoded, encoded_len = asr.forward(tensor, lengths)  # мел-спектр внутри FeatureExtractor
text = asr.decoding.decode(asr.head, encoded, encoded_len)[0]
```

`GigaAMASR.forward(features, feature_lengths)` принимает именно сырой сигнал 16 кГц:
препроцессинг (лог-мел) выполняется внутри модели. Ни одного вызова ffmpeg в рантайме нет.

### 2. Почему не `AutoModel.from_pretrained(trust_remote_code=True)`
Валидатор transformers (`dynamic_module_utils.check_imports`) парсит **все** импорты
remote-файла, включая ленивый `from pyannote.audio import Model` внутри
`transcribe_longform`/`get_pipeline`. pyannote нужен только для необязательного
longform-VAD, которым мы не пользуемся (режем аудио сами), но transformers требует
его установки и падает.

Поэтому движок:
1. импортирует `modeling_gigaam.py` из локального каталога модели через `importlib`;
2. собирает `GigaAMASR` из `config.json` через `hydra`/`omegaconf`
   (путь к `tokenizer.model` подставляет сам);
3. загружает `pytorch_model.bin` (снимает префикс `model.` чекпойнта `GigaAMModel`),
   проверяет `missing`/`unexpected` ключи.

Плюсы: нет валидатора remote-кода, нет копирования кода в `~/.cache`, нет ffmpeg,
меньше зависимостей. На случай изменения формата оставлен fallback через
`AutoModel.from_pretrained` с ослабленной проверкой необязательных импортов
(`_allow_optional_remote_imports`: pyannote не требуется, остальные пакеты — строго).

### 3. HF-кэш внутри данных приложения
`load()` до импорта transformers выставляет `HF_HOME` в `paths.cache_dir()/huggingface`
(если пользователь не задал свой). Так приложение не мусорит в `~/.cache` и работает
на портативной/офлайн-установке. Модель качается одним `snapshot_download`
(revision = `cfg.gigaam_variant`) в `paths.models_dir()/GigaAM-v3` (вариант по умолчанию);
остальные ревизии — в соседние `GigaAM-v3-<variant>`, чтобы бенчмарк держал их рядом.
После успешной загрузки пишется маркер `.golos_model.json` (repo/revision/размер),
чтобы не перекачивать 450 МБ. Прогресс скачивания — через `ProgressFn` (UI/мастер).

### 4. Длинное аудио
Аудио > `cfg.max_chunk_s` (20 c) режется на перекрывающиеся чанки (overlap 0.5 c),
каждый распознаётся отдельно, тексты склеиваются с удалением дублей на стыках
(сравнение нормализованных слов-суффиксов/префиксов). Короткий хвостовой чанк
приклеивается к предыдущему. Проверено на реальной 33-секундной записи: WER 0.0%.

### 5. CPU-потоки
По умолчанию — физические ядра (`psutil.cpu_count(logical=False)`, на Ryzen 7 8845HS
это 8), `torch.set_num_interop_threads(1)`. Замер на 13.9-секундной фразе:
8 потоков — RTF 0.06–0.09, 16 потоков — 0.085–0.12 (hyper-threading конформеру не помогает).
Переопределяется `cfg.torch_threads`.

## Запуск и проверка

```powershell
# из repos/golos, в ограниченном окружении (песочнице) — через dev_env (перенаправляет GOLOS_* в .devdata):
.venv\Scripts\python.exe dev_env.py -- .venv\Scripts\python.exe tools\eval_ru.py

# только конкретные ревизии / с реальными записями:
.venv\Scripts\python.exe tools\eval_ru.py --variants e2e_rnnt,e2e_ctc \
    --real-manifest bench\real\manifest.json

# пересчитать отчёт из готового JSON без прогона моделей:
.venv\Scripts\python.exe tools\eval_ru.py --from-report reports\asr_bench.json

# тесты
.venv\Scripts\python.exe -m pytest tests -q
```

## Ограничения

- faster-whisper прогнан по полному набору (large-v3-turbo int8): WER сопоставим,
  но RTF ~0.9–1.1 (в 13–16 раз медленнее GigaAM) и пунктуация в конце только 42% —
  на CPU-диктовке проигрывает; включается `--whisper`.
- Реальные записи: публичные русские корпуса (Common Voice, Golos) закрыты авторизацией,
  а parquet-датасеты требуют pyarrow (пакеты ставить нельзя). Поэтому в набор вошла
  1 реальная запись из открытого HF-датасета + поддержка `--real-manifest` для будущего.
- Облачный движок проверен юнит-тестами с моками (ключ/HTTP-ошибки/multipart); живой
  ключ в песочнице не проверялся.
