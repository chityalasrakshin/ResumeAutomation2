@echo off
setlocal
pushd "%~dp0"
if not "%~2"=="" (
    echo Usage: run.cmd ["resume file or input folder"] 1>&2
    popd
    exit /b 2
)
if not exist ".venv\Scripts\python.exe" (
    echo Python environment missing. See First-time setup in README.md. 1>&2
    popd
    exit /b 1
)
if "%~1"=="" (
    ".venv\Scripts\python.exe" -m resume_automation --approve-all
) else (
    ".venv\Scripts\python.exe" -m resume_automation --approve-all --inputs "%~1"
)
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
