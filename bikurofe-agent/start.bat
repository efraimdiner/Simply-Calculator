@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  call setup.bat || goto :end
)
if not exist config.toml (
  copy config.example.toml config.toml >nul
  echo Created config.toml - fill in the portal address, save, and run start.bat again.
  notepad config.toml
  goto :end
)
.venv\Scripts\python run.py
:end
pause
