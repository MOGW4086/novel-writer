"""
LINE通知モジュール。
小説生成完了時にLINE Messaging APIを使ってタイトル・ジャンル・テーマ・文字数を通知する。

LINE Notify は2025年3月31日にサービス終了したため、LINE Messaging API（Push Message）を使用する。
必要な環境変数:
    LINE_CHANNEL_ACCESS_TOKEN: LINE Messaging API チャンネルアクセストークン
    LINE_USER_ID:              通知先のLINEユーザーID

リトライ仕様:
    - 最大3回リトライ（初回含む計4回試行）
    - リトライ間隔は指数バックオフ（2秒 → 4秒 → 8秒）
    - 4xx系エラー（認証失敗・無効トークン等）はリトライしない
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# LINE Messaging API Push Messageエンドポイント
_LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

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

    def __post_init__(self) -> None:
        """フィールドの基本バリデーションを行う。"""
        if self.char_count < 0:
            raise ValueError(
                f"char_count は0以上の整数を指定してください（指定値: {self.char_count}）"
            )


def _format_message(payload: NovelNotifyPayload) -> str:
    """
    通知メッセージを整形して返す。

    Args:
        payload: 通知する小説情報。

    Returns:
        整形済みのLINE通知本文。
    """
    return (
        "【小説生成完了】\n"
        f"タイトル：{payload.title}\n"
        f"ジャンル：{payload.genre}\n"
        f"テーマ：{payload.theme}\n"
        f"文字数：{payload.char_count:,}字"
    )


def _get_credentials() -> Tuple[str, str]:
    """
    環境変数から LINE Messaging API の認証情報を取得する。

    Returns:
        (channel_access_token, user_id) のタプル。

    Raises:
        ValueError: 必要な環境変数が未設定の場合。
    """
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.getenv("LINE_USER_ID", "").strip()
    if not token:
        raise ValueError(
            "LINE_CHANNEL_ACCESS_TOKEN が設定されていません。"
            ".env ファイルに LINE_CHANNEL_ACCESS_TOKEN を追加してください。"
        )
    if not user_id:
        raise ValueError(
            "LINE_USER_ID が設定されていません。"
            ".env ファイルに LINE_USER_ID を追加してください。"
        )
    return token, user_id


def _send_once(channel_access_token: str, user_id: str, message: str) -> requests.Response:
    """
    LINE Messaging API に1回リクエストを送信する。

    Args:
        channel_access_token: LINE Messaging API チャンネルアクセストークン。
        user_id: 通知先のLINEユーザーID。
        message: 送信するメッセージ本文。

    Returns:
        レスポンスオブジェクト。
    """
    headers = {
        "Authorization": f"Bearer {channel_access_token}",
        "Content-Type": "application/json",
    }
    body = {
        "to": user_id,
        "messages": [{"type": "text", "text": message}],
    }
    return requests.post(_LINE_PUSH_URL, headers=headers, data=json.dumps(body), timeout=10)


def _is_client_error(exc: requests.RequestException) -> bool:
    """
    例外が4xx系クライアントエラーかどうかを判定する。

    4xx はトークン無効・権限不足など呼び出し側の問題であり、
    リトライしても解決しないため即失敗とする。

    Args:
        exc: 判定対象の例外。

    Returns:
        4xx系エラーであれば True。
    """
    return (
        isinstance(exc, requests.HTTPError)
        and exc.response is not None
        and 400 <= exc.response.status_code < 500
    )


def _do_retry(attempt: int, error: requests.RequestException) -> None:
    """
    リトライの待機・ログ出力を行う共通処理。

    Args:
        attempt: 現在の試行番号（0始まり）。
        error: 発生したエラー。
    """
    if attempt < _MAX_RETRIES:
        wait = _RETRY_BASE_WAIT ** (attempt + 1)
        logger.warning(
            "LINE通知失敗（試行%d回目）。%d秒後にリトライします。エラー: %s",
            attempt + 1,
            wait,
            error,
        )
        time.sleep(wait)
    else:
        logger.error("LINE通知失敗（最大リトライ回数到達）。エラー: %s", error)


def send_novel_notification(payload: NovelNotifyPayload) -> None:
    """
    小説生成完了通知をLINEに送信する。

    4xx系エラー（トークン無効・レート制限等）はリトライせずに即例外を送出する。
    5xx系など一時的なエラーは最大 _MAX_RETRIES 回リトライする。

    Args:
        payload: 通知する小説情報（タイトル・ジャンル・テーマ・文字数）。

    Raises:
        ValueError: 環境変数が未設定の場合、または char_count が負値の場合。
        requests.HTTPError: API呼び出しが最終的に失敗した場合。
        requests.RequestException: ネットワークエラーが続く場合。
    """
    from dotenv import load_dotenv
    load_dotenv()

    channel_access_token, user_id = _get_credentials()
    message = _format_message(payload)

    last_exception: Optional[requests.RequestException] = None

    for attempt in range(_MAX_RETRIES + 1):
        error: Optional[requests.RequestException] = None
        try:
            resp = _send_once(channel_access_token, user_id, message)

            if resp.ok:
                logger.info("LINE通知送信成功（試行%d回目）", attempt + 1)
                return

            # 4xx はリトライしても意味がないため即失敗
            if 400 <= resp.status_code < 500:
                logger.error(
                    "LINE通知失敗（クライアントエラー）: status=%d body=%s",
                    resp.status_code,
                    resp.text,
                )
                resp.raise_for_status()

            # 5xx 等はリトライ対象
            error = requests.HTTPError(
                f"LINE通知失敗: status={resp.status_code} body={resp.text}",
                response=resp,
            )
        except requests.RequestException as exc:
            # 4xx の raise_for_status() はリトライせずそのまま上位へ
            if _is_client_error(exc):
                raise
            error = exc

        # error は必ずここで設定済み（未到達ケースはすべて return/raise 済み）
        assert error is not None
        last_exception = error
        _do_retry(attempt, error)

    raise last_exception  # type: ignore[misc]
