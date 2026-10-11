@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  call setup.bat || goto :end
)
start "Demo portal" .venv\Scripts\python -m mock_portal.app
timeout /t 3 /nobreak >nul
.venv\Scripts\python run.py --config config.demo.toml
:end
pause
