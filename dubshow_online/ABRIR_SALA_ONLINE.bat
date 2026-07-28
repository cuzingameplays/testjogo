@echo off
chcp 65001 >nul
title DubShow Online - Criar sala
cd /d "%~dp0"
echo.
echo ============================================================
echo                    DUBSHOW ONLINE 1.0.2
echo ============================================================
echo O programa criara um link HTTPS e testara o DNS antes de abrir.
echo A primeira preparacao pode baixar ou atualizar componentes.
echo Mantenha esta janela aberta durante toda a partida.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ABRIR_SALA_ONLINE.ps1"
if errorlevel 1 (
  echo.
  echo Ocorreu um erro. Execute DIAGNOSTICAR_TUNEL.bat e consulte a pasta .runtime.
  pause
)
