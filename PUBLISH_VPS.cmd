@echo off
setlocal

cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 publish_vps.py
  goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
  python publish_vps.py
  goto :end
)

if exist "C:\Users\ensgr\AppData\Local\Programs\Python\Python314\python.exe" (
  "C:\Users\ensgr\AppData\Local\Programs\Python\Python314\python.exe" publish_vps.py
  goto :end
)

if exist "C:\Users\ensgr\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "C:\Users\ensgr\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" publish_vps.py
  goto :end
)

echo Python nao foi encontrado nesta maquina.
echo Instale o Python ou rode manualmente com o executavel correto.
pause

:end
endlocal
