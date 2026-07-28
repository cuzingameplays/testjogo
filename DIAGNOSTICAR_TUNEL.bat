@echo off
chcp 65001 >nul
title DubShow Online - Diagnostico do tunel
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0DIAGNOSTICAR_TUNEL.ps1"
