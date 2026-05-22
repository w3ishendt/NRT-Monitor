@echo off
setlocal

cd /d "C:\NRT-Monitor"
"C:\NRT-Monitor\venv\Scripts\python.exe" "C:\NRT-Monitor\agent.py"

pause
endlocal