# Запуск «Голоса» из исходников (для разработки).
#
#   .\run.ps1                 — обычный запуск (трей, данные в %APPDATA%\Golos)
#   .\run.ps1 -Background     — сразу свёрнуто в трей
#   .\run.ps1 -Log            — с консолью и живыми логами
#   .\run.ps1 -DevData        — данные в .devdata (для песочницы/тестов)
#   .\run.ps1 -Cli transcribe sample.wav   — CLI: devices/record/say/transcribe/selftest
param(
    [switch]$Background,
    [switch]$Log,
    [switch]$DevData,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Cli
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv\Scripts"
$python = if ($Log) { Join-Path $venv "python.exe" } else { Join-Path $venv "pythonw.exe" }
if (-not (Test-Path $python)) { throw "Нет окружения .venv — запустите: python tools\bootstrap_env.py" }

$env:PYTHONIOENCODING = "utf-8"
if ($DevData) {
    $dev = Join-Path $root "dev_env.py"
    if (Test-Path $dev) { & (Join-Path $venv "python.exe") $dev | Out-Null }
}

if ($Cli.Count -gt 0) {
    & (Join-Path $venv "python.exe") -m golos.cli @Cli
    exit $LASTEXITCODE
}

$argsList = @("-m", "golos")
if ($Background) { $argsList += "--background" }
if ($Log) {
    & (Join-Path $venv "python.exe") @argsList
} else {
    Start-Process -FilePath $python -ArgumentList $argsList -WorkingDirectory $root
    Write-Host "«Голос» запущен в трее." -ForegroundColor Green
}
