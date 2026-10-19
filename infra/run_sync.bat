@echo off
cd /d "%~dp0.."

set PYTHON_EXEC=python
if exist ".venv2\Scripts\python.exe" set PYTHON_EXEC=.venv2\Scripts\python.exe
if exist ".venv\Scripts\python.exe" set PYTHON_EXEC=.venv\Scripts\python.exe

:loop
echo [%date% %time%] === INICIO CICLO ===
"%PYTHON_EXEC%" -u "app.py"
echo [%date% %time%] === FIN CICLO ===
if exist sync_summary.csv (
	powershell -NoProfile -Command "Get-Content .\sync_summary.csv -Tail 1 | ForEach-Object { $_ -replace ',', ' | ' }"
) else (
	echo No se encontró sync_summary.csv
)
echo [%date% %time%] Esperando 60 segundos...
timeout /t 60 /nobreak >nul
goto loop
