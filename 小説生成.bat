@echo off
:: novel-writer 自動生成バッチ（Windowsタスクスケジューラ用）
:: WSL2 経由で小説生成スクリプトを実行し、ログをファイルに記録する。

wsl -- bash -c "cd /home/kitamiki/novel-writer && source venv/bin/activate && python main.py >> logs/scheduler.log 2>&1"
