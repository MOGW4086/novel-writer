@echo off
:: novel-writer 自動生成バッチ（Windowsタスクスケジューラ用）
:: WSL2 経由で小説生成スクリプトを実行し、ログをファイルに記録する。
:: %~dp0 でバッチファイルのディレクトリを動的取得し、wslpath で Linux パスに変換する。

wsl -- bash -c "cd \"$(wslpath '%~dp0')\" && source venv/bin/activate && python main.py >> logs/scheduler.log 2>&1"
