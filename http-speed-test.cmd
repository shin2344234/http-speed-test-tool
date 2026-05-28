@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%http_speed_test.py"
set "BUNDLED_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

where python >nul 2>nul
if %errorlevel%==0 goto run_python

where py >nul 2>nul
if %errorlevel%==0 goto run_py

if exist "%BUNDLED_PYTHON%" goto run_bundled

echo Python was not found. Install Python 3.9+ or run this from Codex with the bundled runtime available.
exit /b 1

:run_python
python "%SCRIPT%" %*
exit /b %errorlevel%

:run_py
py "%SCRIPT%" %*
exit /b %errorlevel%

:run_bundled
"%BUNDLED_PYTHON%" "%SCRIPT%" %*
exit /b %errorlevel%
