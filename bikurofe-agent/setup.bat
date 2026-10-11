@echo off
cd /d "%~dp0"
echo Installing, this takes a few minutes...
py -3 -m venv .venv || goto :error
.venv\Scripts\python -m pip install -r requirements.txt || goto :error
.venv\Scripts\python -m playwright install chromium || goto :error
echo Done.
exit /b 0
:error
echo Installation failed. Make sure Python 3.11 or newer is installed (python.org) and try again.
exit /b 1
