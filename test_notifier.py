"""
notifier.py のユニットテスト。
requests.post をモックし、実際のAPIを呼び出さずに動作検証する。
"""

import os
import unittest
from unittest.mock import MagicMock, call, patch

import requests

from notifier import (
    NovelNotifyPayload,
    _MAX_RETRIES,
    _do_retry,
    _format_message,
    _get_credentials,
    _is_client_error,
    send_novel_notification,
)


def _make_payload(**kwargs) -> NovelNotifyPayload:
    """テスト用デフォルトペイロードを生成する。"""
    defaults = dict(title="テストタイトル", genre="ファンタジー", theme="冒険", char_count=3000)
    defaults.update(kwargs)
    return NovelNotifyPayload(**defaults)


def _make_response(status_code: int, ok: bool = True, text: str = "") -> MagicMock:
    """テスト用レスポンスモックを生成する。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = ok
    resp.text = text
    return resp


class TestNovelNotifyPayload(unittest.TestCase):
    """NovelNotifyPayload のバリデーションテスト。"""

    def test_正常値で生成できる(self):
        payload = _make_payload(char_count=0)
        self.assertEqual(payload.char_count, 0)

    def test_char_countが負値のときValueError(self):
        with self.assertRaises(ValueError) as ctx:
            _make_payload(char_count=-1)
        self.assertIn("char_count", str(ctx.exception))
        self.assertIn("-1", str(ctx.exception))


class TestFormatMessage(unittest.TestCase):
    """_format_message のフォーマット検証テスト。"""

    def test_全フィールドが含まれる(self):
        payload = _make_payload(
            title="転生勇者の逃走",
            genre="異世界転生",
            theme="勇者召喚からの逃走",
            char_count=4200,
        )
        msg = _format_message(payload)
        self.assertIn("転生勇者の逃走", msg)
        self.assertIn("異世界転生", msg)
        self.assertIn("勇者召喚からの逃走", msg)
        self.assertIn("4,200字", msg)  # 3桁区切りフォーマット

    def test_文字数がカンマ区切りになる(self):
        msg = _format_message(_make_payload(char_count=12345))
        self.assertIn("12,345字", msg)


class TestGetCredentials(unittest.TestCase):
    """_get_credentials の環境変数バリデーションテスト。"""

    def setUp(self):
        # テスト前に対象環境変数をクリア
        os.environ.pop("LINE_CHANNEL_ACCESS_TOKEN", None)
        os.environ.pop("LINE_USER_ID", None)

    def test_両方設定済みで正常取得(self):
        os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "token_abc"
        os.environ["LINE_USER_ID"] = "Uxxxxxxxx"
        token, user_id = _get_credentials()
        self.assertEqual(token, "token_abc")
        self.assertEqual(user_id, "Uxxxxxxxx")

    def test_トークン未設定でValueError(self):
        os.environ["LINE_USER_ID"] = "Uxxxxxxxx"
        with self.assertRaises(ValueError) as ctx:
            _get_credentials()
        self.assertIn("LINE_CHANNEL_ACCESS_TOKEN", str(ctx.exception))

    def test_ユーザーID未設定でValueError(self):
        os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "token_abc"
        with self.assertRaises(ValueError) as ctx:
            _get_credentials()
        self.assertIn("LINE_USER_ID", str(ctx.exception))


class TestIsClientError(unittest.TestCase):
    """_is_client_error の判定テスト。"""

    def _make_http_error(self, status_code: int) -> requests.HTTPError:
        resp = _make_response(status_code, ok=False)
        exc = requests.HTTPError(response=resp)
        return exc

    def test_400はクライアントエラー(self):
        self.assertTrue(_is_client_error(self._make_http_error(400)))

    def test_401はクライアントエラー(self):
        self.assertTrue(_is_client_error(self._make_http_error(401)))

    def test_403はクライアントエラー(self):
        self.assertTrue(_is_client_error(self._make_http_error(403)))

    def test_404はクライアントエラー(self):
        self.assertTrue(_is_client_error(self._make_http_error(404)))

    def test_429はクライアントエラーではなくリトライ対象(self):
        self.assertFalse(_is_client_error(self._make_http_error(429)))

    def test_503はクライアントエラーではない(self):
        self.assertFalse(_is_client_error(self._make_http_error(503)))

    def test_ConnectionErrorはクライアントエラーではない(self):
        self.assertFalse(_is_client_error(requests.ConnectionError()))

    def test_responseなしのHTTPErrorはクライアントエラーではない(self):
        self.assertFalse(_is_client_error(requests.HTTPError()))


class TestDoRetry(unittest.TestCase):
    """_do_retry のログ出力・sleep動作テスト。"""

    def test_途中試行でwarningログとsleepを実行(self):
        err = requests.ConnectionError("接続失敗")
        with patch("notifier.time.sleep") as mock_sleep:
            _do_retry(0, err)
            mock_sleep.assert_called_once_with(2)  # 2^1=2秒

    def test_最終試行でsleepしない(self):
        err = requests.ConnectionError("接続失敗")
        with patch("notifier.time.sleep") as mock_sleep:
            _do_retry(_MAX_RETRIES, err)
            mock_sleep.assert_not_called()


class TestSendNovelNotification(unittest.TestCase):
    """send_novel_notification の統合テスト。"""

    def setUp(self):
        os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "token_abc"
        os.environ["LINE_USER_ID"] = "Uxxxxxxxx"

    def tearDown(self):
        os.environ.pop("LINE_CHANNEL_ACCESS_TOKEN", None)
        os.environ.pop("LINE_USER_ID", None)

    def test_成功レスポンスで正常終了かつリトライしない(self):
        resp = _make_response(200, ok=True)
        with patch("notifier._send_once", return_value=resp) as mock_send:
            send_novel_notification(_make_payload())
            self.assertEqual(mock_send.call_count, 1)

    def test_401でリトライせずHTTPError送出(self):
        resp = _make_response(401, ok=False, text="Unauthorized")
        resp.raise_for_status.side_effect = requests.HTTPError("401", response=resp)
        with patch("notifier._send_once", return_value=resp) as mock_send:
            with self.assertRaises(requests.HTTPError):
                send_novel_notification(_make_payload())
            # リトライなしで1回のみ呼び出される
            self.assertEqual(mock_send.call_count, 1)

    def test_429でリトライするレート制限は時間をおけば回復(self):
        resp = _make_response(429, ok=False, text="Too Many Requests")
        with patch("notifier._send_once", return_value=resp) as mock_send:
            with patch("notifier.time.sleep"):
                with self.assertRaises(requests.HTTPError):
                    send_novel_notification(_make_payload())
            self.assertEqual(mock_send.call_count, 4)  # 初回 + 3回リトライ

    def test_503のリトライ回数が4回(self):
        resp = _make_response(503, ok=False, text="Service Unavailable")
        with patch("notifier._send_once", return_value=resp) as mock_send:
            with patch("notifier.time.sleep"):
                with self.assertRaises(requests.HTTPError):
                    send_novel_notification(_make_payload())
            self.assertEqual(mock_send.call_count, 4)  # 初回 + 3回リトライ

    def test_ネットワークエラーで4回試行する(self):
        with patch("notifier._send_once", side_effect=requests.ConnectionError("接続失敗")) as mock_send:
            with patch("notifier.time.sleep"):
                with self.assertRaises(requests.ConnectionError):
                    send_novel_notification(_make_payload())
            self.assertEqual(mock_send.call_count, 4)

    def test_指数バックオフの待機時間が正しい(self):
        resp = _make_response(503, ok=False, text="Service Unavailable")
        with patch("notifier._send_once", return_value=resp):
            with patch("notifier.time.sleep") as mock_sleep:
                with self.assertRaises(Exception):
                    send_novel_notification(_make_payload())
                # 2^1=2, 2^2=4, 2^3=8 秒
                mock_sleep.assert_has_calls([call(2), call(4), call(8)])

    def test_char_countが負値のときValueError(self):
        with self.assertRaises(ValueError):
            send_novel_notification(_make_payload(char_count=-1))


if __name__ == "__main__":
    unittest.main()
