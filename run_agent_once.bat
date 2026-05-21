@echo off
setlocal

cd /d "C:\NRT_Monitor"
"C:\NRT_Monitor\venv\Scripts\python.exe" "C:\NRT_Monitor\agent.py"

pause
endlocal