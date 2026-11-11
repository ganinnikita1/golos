<#
.SYNOPSIS
    Установка собранного «Голоса» в профиль пользователя.

.DESCRIPTION
    Копирует dist\Golos в %LOCALAPPDATA%\Programs\Golos, создаёт ярлыки
    (меню «Пуск» и рабочий стол) и по желанию включает автозапуск.
    Права администратора не нужны.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\install.ps1
    powershell -ExecutionPolicy Bypass -File packaging\install.ps1 -Autostart -DesktopShortcut
    powershell -ExecutionPolicy Bypass -File packaging\install.ps1 -Uninstall
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\Golos"),
    [switch]$DesktopShortcut,
    [switch]$Autostart,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "dist\Golos"
$exe = Join-Path $InstallDir "Golos.exe"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Голос"
$shortcut = Join-Path $startMenu "Голос.lnk"
$desktopLink = "Голос.lnk"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

# Ярлыки создаём через cscript: PowerShell в ограниченных сессиях не имеет
# доступа к COM (ConstrainedLanguage), а VBScript работает всегда.
function New-Shortcuts([string]$Target) {
    $script = Join-Path $PSScriptRoot "make_shortcut.vbs"
    & cscript //nologo $script $Target
    if ($LASTEXITCODE -ne 0) { Write-Warning "Не удалось создать ярлыки (cscript)" }
}

if ($Uninstall) {
    Write-Host "Удаляю «Голос»..." -ForegroundColor Yellow
    Get-Process -Name "Golos" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $desktopDir = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders").Desktop
    $desktopDir = [string]($desktopDir -replace "%USERPROFILE%", $env:USERPROFILE)
    foreach ($link in @($shortcut, (Join-Path $desktopDir $desktopLink))) {
        if (Test-Path $link) { Remove-Item $link -Force }
    }
    if (Test-Path $startMenu) { Remove-Item $startMenu -Recurse -Force }
    if (Get-ItemProperty -Path $runKey -Name "Golos" -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $runKey -Name "Golos"
    }
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    Write-Host "Удалено. Данные (%APPDATA%\Golos) сохранены — удалите вручную, если не нужны." -ForegroundColor Green
    return
}

if (-not (Test-Path (Join-Path $source "Golos.exe"))) {
    throw "Нет собранного приложения: $source. Сначала запустите packaging\build.ps1"
}

Write-Host "== Установка «Голоса» ==" -ForegroundColor Cyan
Get-Process -Name "Golos" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
New-Item -ItemType Directory -Force $InstallDir | Out-Null
Write-Host "Копирую файлы (это ~750 МБ, может занять минуту)..."
Copy-Item -Path (Join-Path $source "*") -Destination $InstallDir -Recurse -Force

New-Shortcuts -Target $exe

if ($Autostart) {
    Set-ItemProperty -Path $runKey -Name "Golos" -Value ('"{0}" --background' -f $exe)
    Write-Host "Автозапуск включён" -ForegroundColor Green
}

Write-Host ("Установлено: {0}" -f $exe) -ForegroundColor Green
Write-Host "Ярлык: $shortcut"
Write-Host "Запуск: $exe" -ForegroundColor Green
