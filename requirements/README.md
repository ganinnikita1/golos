# Lock-файлы с хешами: как получить и как использовать

Зачем: сейчас `pyproject.toml`, `tools/bootstrap_env.py` и CI ставят зависимости
по плавающим версиям (`transformers>=4.50,<5`, `numpy>=1.26`, `PySide6-Essentials>=6.6`,
`pytest`, `pyinstaller`...) — каждый запуск тянет «то, что сегодня на PyPI».
Это удобно, но:
* сборка невоспроизводима (сегодня тесты зелёные, завтра прилетел мажор зависимости);
* скомпрометированный/угнанный пакет или новая версия попадает к пользователям
  без ревью;
* нельзя включить `pip install --require-hashes`, который проверяет целостность
  скачанных артефактов.

## Шаг 1. Сгенерировать lock-файлы (один раз, на машине с интернетом)

```powershell
cd repos\golos
.\.venv\Scripts\python.exe -m pip install "pip-tools==7.5.2"   # версию закрепить/проверить

# torch/torchaudio — отдельный индекс, без --no-emit-index-url
.\.venv\Scripts\pip-compile.exe --generate-hashes `
    --index-url https://download.pytorch.org/whl/cpu `
    --output-file requirements\torch.lock requirements\torch.in

# всё остальное — с PyPI
.\.venv\Scripts\pip-compile.exe --generate-hashes `
    --no-emit-index-url `
    --output-file requirements\pypi.lock requirements\pypi.in
```

Альтернатива: `uv pip compile --generate-hashes requirements/pypi.in -o requirements/pypi.lock`
(uv тоже поддерживает хеши; закрепите версию uv).

Проверьте, что в `pypi.lock` нет `--extra-index-url`/URL-ов со сторонних индексов.

## Шаг 2. Коммит

```powershell
git add requirements\pypi.in requirements\torch.in requirements\*.lock
git commit -m "chore(deps): lock-файлы с хешами для воспроизводимой установки"
```

Dependabot (см. `proposed/dependabot.yml`) будет обновлять pip-зависимости;
если он не умеет обновлять lock — перегенерируйте lock в том же PR.

## Шаг 3. Использовать в CI и установке

CI/Release workflow (`proposed/ci.yml`, `proposed/release.yml`) уже умеют ставить
из lock, если он есть:

```powershell
python -m pip install --require-hashes -r requirements\torch.lock
python -m pip install --require-hashes -r requirements\pypi.lock
python -m pip install -e ".[dev]" --no-build-isolation --no-deps
```

`bootstrap_env.py` — см. `proposed/bootstrap_env-hardening.diff` (та же схема,
с fallback на текущее поведение, пока lock-ов нет).

## Замечания

* `--require-hashes` работает только при полном наборе пинов; все транзитивные
  зависимости уже будут в lock — руками ничего добавлять не нужно.
* После обновления lock прогоните полный тест-набор и smoke-сборку.
* Lock не «защищает» от злонамеренной новой версии, которую вы сами в него
  добавили; ревью PR Dependabot обязательно.
