@echo off
rem Project WSL path (update here if you move the project)
set WSL_PROJECT=/home/kitamiki/novel-writer

wsl -- bash -c "cd '%WSL_PROJECT%' && source venv/bin/activate && python main.py >> logs/scheduler.log 2>&1"

if %ERRORLEVEL% neq 0 (
    echo Error: generation failed. Check %WSL_PROJECT%/logs/scheduler.log
    pause
)
