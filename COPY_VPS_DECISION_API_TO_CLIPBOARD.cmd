@echo off
setlocal
cd /d "%~dp0"
if not exist "scripts\VPS_SYNC_DECISION_API_MAY04.sh" (
  echo Arquivo scripts\VPS_SYNC_DECISION_API_MAY04.sh nao encontrado.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Get-Content -Raw 'scripts\VPS_SYNC_DECISION_API_MAY04.sh' | Set-Clipboard"
if errorlevel 1 (
  echo Nao foi possivel copiar o script para a area de transferencia.
  pause
  exit /b 1
)
echo Script completo copiado para a area de transferencia.
echo Cole no terminal web da VPS Hostinger e aguarde SYNC_DONE.
pause
