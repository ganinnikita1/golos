# Как безопасно скачать и проверить «Голос»

Для пользователей: как скачать «Голос» и убедиться, что файл не подменён.

## Вариант 1. Готовый архив с GitHub Releases (рекомендуется)

Скачивать только со страницы **https://github.com/ganinnikita1/golos/releases**
официального репозитория. Ссылки из мессенджеров/«зеркал»/форков — не доверять.

Рядом с архивом `Golos-portable-vX.Y.Z.zip` должен лежать `SHA256SUMS.txt`.

### 1. Проверить контрольную сумму

```powershell
# Скачайте Golos-portable-vX.Y.Z.zip и SHA256SUMS.txt в одну папку, затем:
Get-FileHash .\Golos-portable-vX.Y.Z.zip -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

Хеш из `SHA256SUMS.txt` должен совпасть с выводом `Get-FileHash`
(регистр букв не важен). Если не совпал — файл повреждён или подменён, не запускайте.

### 2. Проверить происхождение (Sigstore-аттестация)

GitHub Actions прикладывает к релизу build provenance — криптографическое
подтверждение, что архив собран из коммита `vX.Y.Z` этого репозитория:

```powershell
winget install GitHub.cli        # если gh ещё не установлен
gh attestation verify .\Golos-portable-vX.Y.Z.zip --repo ganinnikita1/golos
```

Ожидаемый вывод: `✓ Verification succeeded!` с источником
`github.com/ganinnikita1/golos` и workflow `Release`.

### 3. Запустить

Распакуйте архив в **новую пустую папку** и запустите `Golos.exe` оттуда
(portable-режим, данные будут в `%APPDATA%\Golos`) либо установите через
`packaging\install.ps1`. Приложение не требует прав администратора.

## Вариант 2. Собрать из исходников самому

Это безопаснее всего: вы видите весь код, который запускаете.

```powershell
git clone --branch vX.Y.Z --depth 1 https://github.com/ganinnikita1/golos.git
cd golos
git rev-parse HEAD          # должен совпасть с коммитом тега на GitHub
python -m venv .venv
.\tools\bootstrap_env.py    # после перехода на lock-файлы ставит точные версии с хешами
.\run.ps1                   # или packaging\build.ps1 + packaging\install.ps1
```

Проверить, что тег не переехал:

```powershell
git ls-remote --tags https://github.com/ganinnikita1/golos.git vX.Y.Z
# SHA тега должен совпадать с тем, что показывает страница релиза/тега на GitHub
```

Если у тега появилась подпись (SSH/GPG):

```powershell
git tag -v vX.Y.Z
```

## Чего не делать

* Не запускать `build.ps1`/`install.ps1`/`run.ps1` из форков, чужих «сборок» и
  архивов, полученных не с официальной страницы Releases.
* Не отключать проверку SmartScreen «навсегда» для незнакомого exe — лучше
  проверить хеш/аттестацию и только потом разрешить запуск.
* Не вводить свои API-ключи (OpenAI/DeepSeek) в чужие сборки; ключи «Голос»
  читает только из переменных окружения вашей учётной записи.
* Не запускать скрипты из репозитория «от администратора» без необходимости:
  установка и работа рассчитаны на обычные права пользователя.
