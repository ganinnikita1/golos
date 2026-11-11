<#
.SYNOPSIS
    Сборка «Голоса» в self-contained папку dist\Golos (PyInstaller one-dir).

.DESCRIPTION
    Требуется собранное окружение .venv (см. tools\bootstrap_env.py).
    Результат: dist\Golos\Golos.exe — двойной клик запускает приложение без консоли.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#>
param(
    [switch]$SkipInstallCheck,
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$dist = Join-Path $root "dist\Golos"
$exe = Join-Path $dist "Golos.exe"

Write-Host "== «Голос»: сборка ==" -ForegroundColor Cyan
if (-not (Test-Path $python)) { throw "Не найден venv: $python. Запустите tools\bootstrap_env.py" }

if (-not $SkipInstallCheck) {
    & $python -c "import PyInstaller, torch, transformers, PySide6" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "В venv не хватает зависимостей для сборки (pyinstaller/torch/transformers/PySide6)." }
}

# Чистим только артефакты сборки, не трогая рабочие файлы.
foreach ($path in @((Join-Path $root "build"), $dist)) {
    if (Test-Path $path) { Remove-Item -Recurse -Force $path }
}

Push-Location $root
try {
    & $python -m PyInstaller --noconfirm --clean --log-level WARN "packaging\golos.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с кодом $LASTEXITCODE" }
} finally {
    Pop-Location
}

if (-not (Test-Path $exe)) { throw "Сборка не дала exe: $exe" }

$sizeMb = [math]::Round(((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ("Готово: {0} ({1} МБ)" -f $exe, $sizeMb) -ForegroundColor Green

if ($Zip) {
    $zipPath = Join-Path $root "dist\Golos-portable.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path $dist -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host ("Портативный архив: {0}" -f $zipPath) -ForegroundColor Green
}
