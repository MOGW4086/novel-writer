"""
app.py エンドポイントのテスト。
FastAPI TestClient を使用してHTTPレベルの動作を検証する。
テスト用DBは一時ファイルを使用し、テスト間の干渉を防ぐ。
"""

import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
