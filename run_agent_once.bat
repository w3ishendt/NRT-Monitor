@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "VBS_FILE=%SCRIPT_DIR%run_agent_once.vbs"

if not exist "%VBS_FILE%" (
	echo Missing launcher: "%VBS_FILE%"
	exit /b 1
)

start "" /min wscript.exe "%VBS_FILE%"
exit /b 0