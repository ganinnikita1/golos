@echo off
rem Двойной клик — запуск «Голоса» из исходников (без консоли).
rem Все аргументы передаются в run.ps1: -Background, -Log, -Cli ...
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
