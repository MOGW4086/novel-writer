#!/bin/bash
# 毎週金曜日の小説自動生成スクリプト。
# cron（毎週金曜9時）と @reboot（起動時の漏れチェック）から呼ばれる。
#
# 動作:
#   今週の金曜日が未実行であれば main.py を実行する。
#   PCが金曜に起動していなかった場合、次回起動時に自動でキャッチアップする。

PROJECT_DIR="/home/kitamiki/novel-writer"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
LAST_RUN_FILE="$PROJECT_DIR/logs/.last_weekly_run"
LOG_FILE="$PROJECT_DIR/logs/cron.log"

# 直近の金曜日の日付を計算（今日が金曜なら今日、それ以外は前の金曜）
today_dow=$(date +%u)  # 月=1 〜 日=7
days_since_friday=$(( (today_dow - 5 + 7) % 7 ))
last_friday=$(date -d "-${days_since_friday} days" +%Y-%m-%d)

# 前回実行日を読み込む（ファイルがなければ未実行扱い）
if [ -f "$LAST_RUN_FILE" ]; then
    last_run=$(cat "$LAST_RUN_FILE")
else
    last_run="1970-01-01"
fi

# 今週の金曜日がまだ未実行なら生成を実行する
if [[ "$last_run" < "$last_friday" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 週次生成を開始します（直近金曜: $last_friday, 前回実行: $last_run）" >> "$LOG_FILE"
    cd "$PROJECT_DIR" || exit 1
    "$VENV_PYTHON" main.py >> "$LOG_FILE" 2>&1
    exit_code=$?
    if [ $exit_code -eq 0 ]; then
        # 成功時のみ実行日を更新する（失敗時は次回起動で再挑戦）
        echo "$last_friday" > "$LAST_RUN_FILE"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 週次生成が完了しました" >> "$LOG_FILE"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 週次生成が失敗しました（終了コード: $exit_code）" >> "$LOG_FILE"
    fi
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 今週の生成は実行済みです（前回実行: $last_run）" >> "$LOG_FILE"
fi
