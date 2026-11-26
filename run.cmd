@echo off
setlocal
if not "%~2"=="" (
    echo Usage: run.cmd ["resume file or input folder"] 1>&2
    exit /b 2
)
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Python environment missing. See First-time setup in README.md. 1>&2
    exit /b 1
)
if "%~1"=="" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0batch_run.py" --approve-all
) else (
    "%~dp0.venv\Scripts\python.exe" "%~dp0batch_run.py" --approve-all --inputs "%~1"
)
exit /b %errorlevel%
