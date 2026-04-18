@echo off
:: novel-reader 起動バッチ（デスクトップショートカット用）
:: WSL2 経由で FastAPI サーバーを起動し、ブラウザで閲覧ページを開く。

echo 小説サーバーを起動中...

:: 新しいウィンドウでサーバーを起動（ウィンドウを閉じるとサーバーも停止）
start "novel-reader サーバー" wsl -- bash -c "cd /home/kitamiki/novel-writer && source venv/bin/activate && uvicorn app:app --host 127.0.0.1 --port 8000"

:: サーバー起動を待機（3秒）
timeout /t 3 /nobreak > nul

:: ブラウザで閲覧ページを開く
start http://localhost:8000

echo ブラウザが開きます。サーバーを停止するには「novel-reader サーバー」ウィンドウを閉じてください。
