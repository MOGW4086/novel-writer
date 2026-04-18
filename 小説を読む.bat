@echo off
:: ============================================================
:: プロジェクトのWSLパス（プロジェクトを移動した場合はここを修正）
set WSL_PROJECT=/home/kitamiki/novel-writer
:: ============================================================

echo 小説サーバーを起動中...

:: 新しいウィンドウでサーバーを起動（ウィンドウを閉じるとサーバーも停止）
start "novel-reader サーバー" wsl -- bash -c "cd '%WSL_PROJECT%' && source venv/bin/activate && uvicorn app:app --host 127.0.0.1 --port 8000"

if %ERRORLEVEL% neq 0 (
    echo [エラー] サーバーの起動に失敗しました。WSLが有効になっているか確認してください。
    pause
    exit /b 1
)

:: サーバー起動を待機（3秒）
timeout /t 3 /nobreak > nul

:: ブラウザで閲覧ページを開く
start http://localhost:8000

echo ブラウザが開きます。サーバーを停止するには「novel-reader サーバー」ウィンドウを閉じてください。
