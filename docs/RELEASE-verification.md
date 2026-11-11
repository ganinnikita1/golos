# Как безопасно скачать и проверить «Голос»

Для пользователей: как скачать «Голос» и убедиться, что файл не подменён.
Актуально для релизов **v0.1.2 и новее** (они публикуются как immutable releases и имеют
release-attestation). Для старого `v0.1.1` см. примечание в конце.

## Вариант 1. Готовый архив с GitHub Releases (рекомендуется)

Скачивать только со страницы **https://github.com/ganinnikita1/golos/releases**
официального репозитория. Ссылки из мессенджеров/«зеркал»/форков — не доверять.

Рядом с архивом `Golos-portable-vX.Y.Z.zip` лежат `SHA256SUMS-vX.Y.Z.txt`
и `golos-vX.Y.Z-source.zip`.

### 1. Проверить контрольную сумму

```powershell
# Скачайте Golos-portable-vX.Y.Z.zip и SHA256SUMS-vX.Y.Z.txt в одну папку, затем:
Get-FileHash .\Golos-portable-vX.Y.Z.zip -Algorithm SHA256
Get-Content .\SHA256SUMS-vX.Y.Z.txt
```

Хеш из `SHA256SUMS-vX.Y.Z.txt` должен совпасть с выводом `Get-FileHash`
(регистр букв не важен). Если не совпал — файл повреждён или подменён, не запускайте.

### 2. Проверить, что релиз не подменён (immutable release + attestation)

У immutable-релиза GitHub автоматически создаёт release-attestation, а тег и ассеты
нельзя изменить после публикации:

```powershell
winget install GitHub.cli        # если gh ещё не установлен
# релиз существует и является immutable
gh release verify vX.Y.Z
# скачанный файл — точная копия ассета из релиза
gh release verify-asset vX.Y.Z .\Golos-portable-vX.Y.Z.zip
gh release verify-asset vX.Y.Z .\golos-vX.Y.Z-source.zip
```

Ожидаемый вывод: успешная проверка с источником `github.com/ganinnikita1/golos`.
Если команды `gh release verify` нет — обновите GitHub CLI (`winget upgrade GitHub.cli`).

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
.\tools\bootstrap_env.py    # после появления lock-файлов — точные версии с хешами
.\run.ps1                   # или packaging\build.ps1 + packaging\install.ps1
```

Проверить, что тег не переехал:

```powershell
git ls-remote --tags https://github.com/ganinnikita1/golos.git vX.Y.Z
# SHA тега должен совпадать с тем, что показывает страница релиза/тега на GitHub
```

Если тег подписан (SSH/GPG):

```powershell
git verify-tag vX.Y.Z
```

## Примечание про v0.1.1

`v0.1.1` опубликован **до включения immutable releases** и **без attestation** (`gh release
verify` для него не пройдёт). Для него остаётся проверка SHA256 по `SHA256SUMS-v0.1.1.txt`.
Дополнительно известно, что внутри `v0.1.1` portable-zip в служебном `.pyc` остался путь
сборочной машины (без ключей/доступа) — это исправлено начиная с v0.1.2 (сборка в CI).

## Чего не делать

* Не запускать `build.ps1`/`install.ps1`/`run.ps1` из форков, чужих «сборок» и
  архивов, полученных не с официальной страницы Releases.
* Не отключать SmartScreen «навсегда» для незнакомого exe — проверьте хеш/attestation
  и только потом разрешайте запуск.
* Не вводить свои API-ключи (OpenAI/DeepSeek) в чужие сборки; ключи «Голос»
  читает только из переменных окружения вашей учётной записи.
* Не запускать скрипты из репозитория «от администратора» без необходимости:
  установка и работа рассчитаны на обычные права пользователя.
