@echo off
:: ============================================================
:: プロジェクトのWSLパス（プロジェクトを移動した場合はここを修正）
set WSL_PROJECT=/home/kitamiki/novel-writer
:: ============================================================

wsl -- bash -c "cd '%WSL_PROJECT%' && source venv/bin/activate && python main.py >> logs/scheduler.log 2>&1"

if %ERRORLEVEL% neq 0 (
    echo [エラー] 小説生成に失敗しました。ログを確認してください。
    echo ログの場所: %WSL_PROJECT%/logs/scheduler.log
    pause
)
