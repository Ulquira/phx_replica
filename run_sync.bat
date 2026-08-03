@echo off
cd /d "c:\Users\user\Desktop\Api_phx"
:loop
echo [%date% %time%] === INICIO CICLO ===
".venv\Scripts\python.exe" -u "sync_azure_mysql_local.py"
echo [%date% %time%] === FIN CICLO ===
if exist sync_summary.csv (
	powershell -NoProfile -Command "Get-Content .\sync_summary.csv -Tail 1 | ForEach-Object { $_ -replace ',', ' | ' }"
) else (
	echo No se encontró sync_summary.csv
)
echo [%date% %time%] Esperando 60 segundos...
timeout /t 60 /nobreak >nul
goto loop
