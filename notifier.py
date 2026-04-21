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
    - 400/401/403/404 は呼び出し側の設定ミスのため即失敗
    - 429（レート制限）・5xx は一時的なエラーのためリトライ対象
"""

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

# リトライしない4xxステータスコード（設定ミス・権限不足など、時間をおいても解決しないもの）
# 429（レート制限）は時間をおけば回復するためリトライ対象とする
_NON_RETRYABLE_4XX = frozenset({400, 401, 403, 404})


@dataclass
class NovelNotifyPayload:
    """通知に必要な小説情報をまとめたデータクラス。"""
    title: str
    genre: str
    theme: str
    char_count: int
    series_name: Optional[str] = None
    episode_number: Optional[int] = None

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
    lines = [
        "【小説生成完了】",
        f"タイトル：{payload.title}",
        f"ジャンル：{payload.genre}",
        f"テーマ：{payload.theme}",
        f"文字数：{payload.char_count:,}字",
    ]
    if payload.series_name:
        ep = f" 第{payload.episode_number}話" if payload.episode_number is not None else ""
        lines.append(f"シリーズ：{payload.series_name}{ep}")
    return "\n".join(lines)


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
    body = {
        "to": user_id,
        "messages": [{"type": "text", "text": message}],
    }
    # json= パラメータにより requests が Content-Type: application/json を自動設定する
    return requests.post(
        _LINE_PUSH_URL,
        headers={"Authorization": f"Bearer {channel_access_token}"},
        json=body,
        timeout=10,
    )


def _is_client_error(exc: requests.RequestException) -> bool:
    """
    例外がリトライしても解決しないクライアントエラーかどうかを判定する。

    _NON_RETRYABLE_4XX（400/401/403/404）はトークン無効・権限不足など
    設定ミスに起因するため即失敗とする。
    429（レート制限）は時間をおけば回復するためリトライ対象とする。

    Args:
        exc: 判定対象の例外。

    Returns:
        リトライしないクライアントエラーであれば True。
    """
    return (
        isinstance(exc, requests.HTTPError)
        and exc.response is not None
        and exc.response.status_code in _NON_RETRYABLE_4XX
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

    400/401/403/404 はリトライせずに即例外を送出する。
    429・5xx系など一時的なエラーは最大 _MAX_RETRIES 回リトライする。

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

            # リトライしないクライアントエラーは即失敗
            if resp.status_code in _NON_RETRYABLE_4XX:
                logger.error(
                    "LINE通知失敗（クライアントエラー）: status=%d body=%s",
                    resp.status_code,
                    resp.text,
                )
                resp.raise_for_status()

            # 429・5xx 等はリトライ対象
            error = requests.HTTPError(
                f"LINE通知失敗: status={resp.status_code} body={resp.text}",
                response=resp,
            )
        except requests.RequestException as exc:
            # リトライしないクライアントエラーの raise_for_status() はそのまま上位へ
            if _is_client_error(exc):
                raise
            error = exc

        # error は必ずここで設定済み（未到達ケースはすべて return/raise 済み）
        if error is None:
            raise RuntimeError("予期しないコードパス: error が未設定です")
        last_exception = error
        _do_retry(attempt, error)

    assert last_exception is not None  # ループが0回以上実行された保証（型検査用）
    raise last_exception


def send_extraction_error_notification(
    consecutive_failures: int,
    error_type: str,
    error_message: str,
) -> None:
    """
    知見抽出の連続失敗をLINEに通知する。
    通知自体の失敗はログのみ記録し、例外を上位に伝播させない。

    Args:
        consecutive_failures: 現在の連続失敗件数。
        error_type: 最新エラーの種別。
        error_message: 最新エラーのメッセージ。
    """
    from dotenv import load_dotenv
    load_dotenv()

    try:
        channel_access_token, user_id = _get_credentials()
    except ValueError:
        logger.warning("LINE認証情報が未設定のため知見抽出エラー通知をスキップします")
        return

    # LINE APIは5000文字制限のため、長いエラーメッセージを切り詰める
    safe_error_message = (error_message[:1000] + "...") if len(error_message) > 1000 else error_message

    message = (
        f"【知見抽出エラー通知】\n"
        f"連続失敗回数: {consecutive_failures}回\n"
        f"エラー種別: {error_type}\n"
        f"メッセージ: {safe_error_message}"
    )

    try:
        resp = _send_once(channel_access_token, user_id, message)
        if resp.ok:
            logger.info("知見抽出エラー通知を送信しました（連続失敗%d回）", consecutive_failures)
        else:
            logger.warning(
                "知見抽出エラー通知の送信に失敗しました: status=%d", resp.status_code
            )
    except requests.RequestException as exc:
        logger.warning("知見抽出エラー通知の送信中に例外が発生しました: %s", exc)
