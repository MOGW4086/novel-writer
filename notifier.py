"""
LINE通知モジュール。
小説生成完了時にLINE Notify APIを使ってタイトル・ジャンル・テーマ・文字数を通知する。

リトライ仕様:
    - 最大3回リトライ（初回含む計4回試行）
    - リトライ間隔は指数バックオフ（2秒 → 4秒 → 8秒）
    - 4xx系エラー（認証失敗・無効トークン等）はリトライしない
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# LINE Notify APIのエンドポイント
_LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"

# リトライ設定
_MAX_RETRIES = 3       # 初回失敗後の最大リトライ回数
_RETRY_BASE_WAIT = 2   # 指数バックオフの基本秒数（2 → 4 → 8 秒）


@dataclass
class NovelNotifyPayload:
    """通知に必要な小説情報をまとめたデータクラス。"""
    title: str
    genre: str
    theme: str
    char_count: int


def _format_message(payload: NovelNotifyPayload) -> str:
    """
    通知メッセージを整形して返す。

    Args:
        payload: 通知する小説情報。

    Returns:
        整形済みのLINE通知本文。
    """
    return (
        "\n"
        "【小説生成完了】\n"
        f"タイトル：{payload.title}\n"
        f"ジャンル：{payload.genre}\n"
        f"テーマ：{payload.theme}\n"
        f"文字数：{payload.char_count:,}字"
    )


def _get_token() -> str:
    """
    環境変数から LINE Notify トークンを取得する。

    Returns:
        LINE Notify アクセストークン文字列。

    Raises:
        ValueError: LINE_NOTIFY_TOKEN が未設定の場合。
    """
    token = os.getenv("LINE_NOTIFY_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "LINE_NOTIFY_TOKEN が設定されていません。"
            ".env ファイルに LINE_NOTIFY_TOKEN を追加してください。"
        )
    return token


def _send_once(token: str, message: str) -> requests.Response:
    """
    LINE Notify APIに1回リクエストを送信する。

    Args:
        token: LINE Notify アクセストークン。
        message: 送信するメッセージ本文。

    Returns:
        レスポンスオブジェクト。
    """
    headers = {"Authorization": f"Bearer {token}"}
    data = {"message": message}
    return requests.post(_LINE_NOTIFY_URL, headers=headers, data=data, timeout=10)


def send_novel_notification(payload: NovelNotifyPayload) -> None:
    """
    小説生成完了通知をLINEに送信する。

    4xx系エラー（トークン無効・レート制限等）はリトライせずに即例外を送出する。
    5xx系など一時的なエラーは最大 _MAX_RETRIES 回リトライする。

    Args:
        payload: 通知する小説情報（タイトル・ジャンル・テーマ・文字数）。

    Raises:
        ValueError: LINE_NOTIFY_TOKEN が未設定の場合。
        requests.HTTPError: API呼び出しが最終的に失敗した場合。
        requests.RequestException: ネットワークエラーが続く場合。
    """
    token = _get_token()
    message = _format_message(payload)

    last_exception: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES + 1):
        # ネットワークエラー（接続失敗・タイムアウト等）はリトライ対象
        try:
            resp = _send_once(token, message)
        except requests.RequestException as exc:
            last_exception = exc
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BASE_WAIT ** (attempt + 1)
                logger.warning(
                    "LINE通知失敗（試行%d回目）。%d秒後にリトライします。エラー: %s",
                    attempt + 1,
                    wait,
                    exc,
                )
                time.sleep(wait)
            else:
                logger.error("LINE通知失敗（最大リトライ回数到達）。エラー: %s", exc)
            continue

        # 4xx はリトライしても意味がないため即失敗
        if 400 <= resp.status_code < 500:
            logger.error(
                "LINE通知失敗（クライアントエラー）: status=%d body=%s",
                resp.status_code,
                resp.text,
            )
            resp.raise_for_status()

        # 2xx 以外（5xx 等）はリトライ対象
        if not resp.ok:
            last_exception = requests.HTTPError(
                f"LINE通知失敗: status={resp.status_code} body={resp.text}",
                response=resp,
            )
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BASE_WAIT ** (attempt + 1)
                logger.warning(
                    "LINE通知失敗（試行%d回目）。%d秒後にリトライします。エラー: %s",
                    attempt + 1,
                    wait,
                    last_exception,
                )
                time.sleep(wait)
            else:
                logger.error("LINE通知失敗（最大リトライ回数到達）。エラー: %s", last_exception)
            continue

        logger.info("LINE通知送信成功（試行%d回目）", attempt + 1)
        return

    raise last_exception  # type: ignore[misc]
