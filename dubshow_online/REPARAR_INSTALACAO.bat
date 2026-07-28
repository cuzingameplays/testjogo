@echo off
chcp 65001 >nul
title DubShow Online - Reparar instalacao
cd /d "%~dp0"
echo.
echo Esta ferramenta apaga apenas os componentes baixados.
echo Seus arquivos do jogo nao serao removidos.
echo.
if exist ".runtime" rmdir /s /q ".runtime"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALAR_ONLINE.ps1"
if errorlevel 1 (
  echo.
  echo A reparacao falhou. Verifique sua conexao e tente novamente.
  pause
  exit /b 1
)
echo.
echo Reparacao concluida. Agora abra ABRIR_SALA_ONLINE.bat.
pause
