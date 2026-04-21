"""
app.py エンドポイントのテスト。
FastAPI TestClient を使用してHTTPレベルの動作を検証する。
テスト用DBは一時ファイルを使用し、テスト間の干渉を防ぐ。
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


def _make_client(tmp_db_path: str) -> TestClient:
    """一時DBを指定してアプリクライアントを生成する。"""
    os.environ["DB_PATH"] = tmp_db_path
    # DB_PATH をセット後に app をインポートしないと db モジュールが古いパスを参照するため
    # importlib でリロードしてモジュールキャッシュをリセットする
    import importlib
    import db as db_mod
    import app as app_mod
    importlib.reload(db_mod)
    importlib.reload(app_mod)
    db_mod.init_db()
    return TestClient(app_mod.app)


class TestIndexPage(unittest.TestCase):
    """トップページのテスト。"""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.client = _make_client(self._tmp.name)

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_空の状態で200を返す(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)

    def test_小説がある場合にタイトルが含まれる(self):
        import db as db_mod
        db_mod.save_novel("テスト小説", "ファンタジー", "冒険", "本文テスト")
        res = self.client.get("/")
        self.assertIn("テスト小説", res.text)


class TestNovelDetail(unittest.TestCase):
    """小説閲覧ページのテスト。"""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.client = _make_client(self._tmp.name)
        import db as db_mod
        self.novel_id = db_mod.save_novel("閲覧テスト", "SF", "宇宙", "本文内容テスト")

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_存在する小説は200を返す(self):
        res = self.client.get(f"/novels/{self.novel_id}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("閲覧テスト", res.text)

    def test_存在しない小説は404を返す(self):
        res = self.client.get("/novels/99999")
        self.assertEqual(res.status_code, 404)

    def test_初回アクセスで読書進捗が作成される(self):
        import db as db_mod
        self.assertIsNone(db_mod.get_reading_progress(self.novel_id))
        self.client.get(f"/novels/{self.novel_id}")
        progress = db_mod.get_reading_progress(self.novel_id)
        self.assertIsNotNone(progress)
        self.assertEqual(progress["scroll_percent"], 0)


class TestUpdateProgress(unittest.TestCase):
    """読書進捗更新エンドポイントのテスト。"""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.client = _make_client(self._tmp.name)
        import db as db_mod
        self.novel_id = db_mod.save_novel("進捗テスト", "恋愛", "片想い", "本文")

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_正常な進捗を更新できる(self):
        res = self.client.post(
            f"/novels/{self.novel_id}/progress",
            json={"scroll_percent": 50},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["scroll_percent"], 50)
        self.assertFalse(data["is_completed"])

    def test_95以上で読了になる(self):
        res = self.client.post(
            f"/novels/{self.novel_id}/progress",
            json={"scroll_percent": 100},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["is_completed"])

    def test_範囲外の値は422を返す(self):
        res = self.client.post(
            f"/novels/{self.novel_id}/progress",
            json={"scroll_percent": 150},
        )
        self.assertEqual(res.status_code, 422)

    def test_存在しない小説は404を返す(self):
        res = self.client.post(
            "/novels/99999/progress",
            json={"scroll_percent": 50},
        )
        self.assertEqual(res.status_code, 404)


class TestSubmitFeedback(unittest.TestCase):
    """フィードバック送信エンドポイントのテスト。"""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.client = _make_client(self._tmp.name)
        import db as db_mod
        self.db_mod = db_mod
        self.novel_id = db_mod.save_novel("フィードバックテスト", "ホラー", "怪談", "本文")

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_正常なフィードバックは303でリダイレクト(self):
        res = self.client.post(
            f"/novels/{self.novel_id}/feedback",
            data={"rating": "4", "comment": "面白かった"},
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 303)

    def test_フィードバックがDBに保存される(self):
        import db as db_mod
        self.client.post(
            f"/novels/{self.novel_id}/feedback",
            data={"rating": "3", "comment": "普通"},
        )
        feedbacks = db_mod.get_feedback(self.novel_id)
        self.assertEqual(len(feedbacks), 1)
        self.assertEqual(feedbacks[0]["rating"], 3)
        self.assertEqual(feedbacks[0]["comment"], "普通")

    def test_範囲外のratingは422を返す(self):
        res = self.client.post(
            f"/novels/{self.novel_id}/feedback",
            data={"rating": "6"},
        )
        self.assertEqual(res.status_code, 422)

    def test_存在しない小説は404を返す(self):
        res = self.client.post(
            "/novels/99999/feedback",
            data={"rating": "3"},
        )
        self.assertEqual(res.status_code, 404)

    def test_コメントありのフィードバックで知見抽出が呼ばれる(self):
        with patch("knowledge.extract_and_save_knowledge") as mock_extract:
            self.client.post(
                f"/novels/{self.novel_id}/feedback",
                data={"rating": "5", "comment": "文体が素晴らしい"},
                follow_redirects=False,
            )
            mock_extract.assert_called_once_with("文体が素晴らしい", novel_id=self.novel_id)

    def test_コメントなしのフィードバックでは知見抽出が呼ばれない(self):
        with patch("knowledge.extract_and_save_knowledge") as mock_extract:
            self.client.post(
                f"/novels/{self.novel_id}/feedback",
                data={"rating": "3", "comment": ""},
                follow_redirects=False,
            )
            mock_extract.assert_not_called()

    def test_知見抽出が失敗してもフィードバックは保存される(self):
        with patch("knowledge.extract_and_save_knowledge", side_effect=Exception("API error")):
            res = self.client.post(
                f"/novels/{self.novel_id}/feedback",
                data={"rating": "4", "comment": "良かった"},
                follow_redirects=False,
            )
        # 知見抽出が失敗しても303リダイレクトされる
        self.assertEqual(res.status_code, 303)
        feedbacks = self.db_mod.get_feedback(self.novel_id)
        self.assertEqual(len(feedbacks), 1)
        self.assertEqual(feedbacks[0]["comment"], "良かった")


class TestSeriesDetail(unittest.TestCase):
    """シリーズ詳細ページのテスト。"""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.client = _make_client(self._tmp.name)
        import db as db_mod
        self.series_id = db_mod.create_series("テストシリーズ", "シリーズ説明")
        db_mod.save_novel("第1話", "ファンタジー", "冒険", "本文", series_id=self.series_id, episode_number=1)

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_存在するシリーズは200を返す(self):
        res = self.client.get(f"/series/{self.series_id}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("テストシリーズ", res.text)

    def test_存在しないシリーズは404を返す(self):
        res = self.client.get("/series/99999")
        self.assertEqual(res.status_code, 404)


class TestReadingProgressCRUD(unittest.TestCase):
    """reading_progress テーブルの CRUD 関数テスト。"""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        os.environ["DB_PATH"] = self._tmp.name
        import importlib
        import db as db_mod
        importlib.reload(db_mod)
        db_mod.init_db()
        self.db = db_mod
        self.novel_id = db_mod.save_novel("進捗CRUDテスト", "SF", "宇宙", "本文")

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_未読の場合はNoneを返す(self):
        self.assertIsNone(self.db.get_reading_progress(self.novel_id))

    def test_upsertで進捗を作成できる(self):
        self.db.upsert_reading_progress(self.novel_id, 30)
        progress = self.db.get_reading_progress(self.novel_id)
        self.assertIsNotNone(progress)
        self.assertEqual(progress["scroll_percent"], 30)
        self.assertEqual(progress["is_completed"], 0)

    def test_upsertで進捗を更新できる(self):
        self.db.upsert_reading_progress(self.novel_id, 30)
        self.db.upsert_reading_progress(self.novel_id, 70)
        progress = self.db.get_reading_progress(self.novel_id)
        self.assertEqual(progress["scroll_percent"], 70)

    def test_読了フラグは一度Trueになると戻らない(self):
        self.db.upsert_reading_progress(self.novel_id, 100, is_completed=True)
        self.db.upsert_reading_progress(self.novel_id, 50, is_completed=False)
        progress = self.db.get_reading_progress(self.novel_id)
        self.assertEqual(progress["is_completed"], 1)

    def test_standalone_novelsにopened_atが含まれる(self):
        # opened_at は reading_progress JOIN で付与されるため、未読は None になる
        novels = self.db.get_standalone_novels()
        self.assertEqual(len(novels), 1)
        self.assertIsNone(novels[0].get("opened_at"))
        # 進捗を作成後は None でなくなる
        self.db.upsert_reading_progress(self.novel_id, 0)
        novels = self.db.get_standalone_novels()
        self.assertIsNotNone(novels[0].get("opened_at"))


class TestExtractionLog(unittest.TestCase):
    """知見抽出ログの記録とLINE通知のテスト。"""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.client = _make_client(self._tmp.name)
        import db as db_mod
        self.db_mod = db_mod
        self.novel_id = db_mod.save_novel("抽出ログテスト", "ファンタジー", "冒険", "本文")

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_成功時にsuccessログが保存される(self):
        with patch("knowledge.extract_and_save_knowledge"):
            self.client.post(
                f"/novels/{self.novel_id}/feedback",
                data={"rating": "5", "comment": "良かった"},
                follow_redirects=False,
            )
        with self.db_mod.get_connection() as conn:
            logs = conn.execute("SELECT status FROM extraction_logs").fetchall()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "success")

    def test_失敗時にfailureログが保存される(self):
        with patch("knowledge.extract_and_save_knowledge", side_effect=ValueError("test error")):
            with patch("notifier.send_extraction_error_notification"):
                self.client.post(
                    f"/novels/{self.novel_id}/feedback",
                    data={"rating": "3", "comment": "コメント"},
                    follow_redirects=False,
                )
        with self.db_mod.get_connection() as conn:
            logs = conn.execute("SELECT status, error_type FROM extraction_logs").fetchall()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "failure")
        self.assertEqual(logs[0]["error_type"], "ValueError")

    def test_連続失敗カウントが閾値に達するとLINE通知が送られる(self):
        with patch("knowledge.extract_and_save_knowledge", side_effect=Exception("API error")):
            with patch("notifier.send_extraction_error_notification") as mock_notify:
                # 閾値（3回）に達するまでは通知しない
                for _ in range(2):
                    self.client.post(
                        f"/novels/{self.novel_id}/feedback",
                        data={"rating": "2", "comment": "コメント"},
                        follow_redirects=False,
                    )
                mock_notify.assert_not_called()
                # 3回目で通知
                self.client.post(
                    f"/novels/{self.novel_id}/feedback",
                    data={"rating": "2", "comment": "コメント"},
                    follow_redirects=False,
                )
                mock_notify.assert_called_once()
                args = mock_notify.call_args[0]
                self.assertEqual(args[0], 3)  # consecutive_failures

    def test_成功後は連続失敗カウントがリセットされる(self):
        # 2回失敗させた後に成功させる
        with patch("knowledge.extract_and_save_knowledge", side_effect=Exception("error")):
            with patch("notifier.send_extraction_error_notification"):
                for _ in range(2):
                    self.client.post(
                        f"/novels/{self.novel_id}/feedback",
                        data={"rating": "2", "comment": "コメント"},
                        follow_redirects=False,
                    )
        with patch("knowledge.extract_and_save_knowledge"):
            self.client.post(
                f"/novels/{self.novel_id}/feedback",
                data={"rating": "5", "comment": "コメント"},
                follow_redirects=False,
            )
        self.assertEqual(self.db_mod.get_consecutive_failure_count(), 0)

    def test_コメントなしの場合は抽出ログが保存されない(self):
        self.client.post(
            f"/novels/{self.novel_id}/feedback",
            data={"rating": "3", "comment": ""},
            follow_redirects=False,
        )
        with self.db_mod.get_connection() as conn:
            logs = conn.execute("SELECT * FROM extraction_logs").fetchall()
        self.assertEqual(len(logs), 0)


class TestExtractionLogCRUD(unittest.TestCase):
    """db.save_extraction_log / get_consecutive_failure_count の単体テスト。"""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        os.environ["DB_PATH"] = self._tmp.name
        import importlib
        import db as db_mod
        importlib.reload(db_mod)
        db_mod.init_db()
        self.db = db_mod
        self.novel_id = db_mod.save_novel("ログCRUDテスト", "SF", "宇宙", "本文")

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_ログが空のとき連続失敗は0(self):
        self.assertEqual(self.db.get_consecutive_failure_count(), 0)

    def test_successが最新のとき連続失敗は0(self):
        self.db.save_extraction_log(self.novel_id, "failure")
        self.db.save_extraction_log(self.novel_id, "success")
        self.assertEqual(self.db.get_consecutive_failure_count(), 0)

    def test_failureが連続するとその件数を返す(self):
        self.db.save_extraction_log(self.novel_id, "success")
        self.db.save_extraction_log(self.novel_id, "failure")
        self.db.save_extraction_log(self.novel_id, "failure")
        self.db.save_extraction_log(self.novel_id, "failure")
        self.assertEqual(self.db.get_consecutive_failure_count(), 3)

    def test_エラー情報が保存される(self):
        self.db.save_extraction_log(
            self.novel_id, "failure",
            error_type="ValueError", error_message="テストエラー"
        )
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM extraction_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row["error_type"], "ValueError")
        self.assertEqual(row["error_message"], "テストエラー")


if __name__ == "__main__":
    unittest.main()
