@echo off
rem Project WSL path (update here if you move the project)
set WSL_PROJECT=/home/kitamiki/novel-writer

echo Starting novel-reader server...
start "novel-reader" wsl -- bash -c "cd '%WSL_PROJECT%' && source venv/bin/activate && uvicorn app:app --host 127.0.0.1 --port 8000"

timeout /t 3 /nobreak > nul
start http://localhost:8000
echo Browser opening... Close the 'novel-reader' window to stop the server.
