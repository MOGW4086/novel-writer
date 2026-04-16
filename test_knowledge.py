"""
knowledge.py のユニットテスト。
Claude API と DB への依存をモックして動作検証する。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import anthropic

import db
from knowledge import (
    VALID_CATEGORIES,
    _call_claude,
    _get_client,
    _load_knowledge_config,
    _parse_insights,
    extract_and_save_knowledge,
    get_knowledge_for_prompt,
)


class TestParseInsights(unittest.TestCase):
    """_parse_insights のパース・バリデーションテスト。"""

    def test_正常なJSON配列をパースできる(self):
        raw = '[{"category": "文体", "insight": "テンポよく書く"}]'
        result = _parse_insights(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "文体")
        self.assertEqual(result[0]["insight"], "テンポよく書く")

    def test_前置きテキストがあっても抽出できる(self):
        raw = 'はい、抽出します。\n[{"category": "キャラ", "insight": "個性的にする"}]'
        result = _parse_insights(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "キャラ")

    def test_コードブロックがあっても抽出できる(self):
        raw = '```json\n[{"category": "構成", "insight": "起承転結を明確にする"}]\n```'
        result = _parse_insights(raw)
        self.assertEqual(len(result), 1)

    def test_全カテゴリを含む入力を処理できる(self):
        raw = """[
            {"category": "文体", "insight": "会話文を増やす"},
            {"category": "キャラ", "insight": "主人公を明確にする"},
            {"category": "構成", "insight": "伏線を張る"},
            {"category": "ジャンル", "insight": "ジャンル定番要素を押さえる"}
        ]"""
        result = _parse_insights(raw)
        self.assertEqual(len(result), 4)
        categories = {r["category"] for r in result}
        self.assertEqual(categories, VALID_CATEGORIES)

    def test_無効なカテゴリを除外する(self):
        raw = '[{"category": "その他", "insight": "XXX"}, {"category": "文体", "insight": "YYY"}]'
        result = _parse_insights(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "文体")

    def test_insightが空のエントリを除外する(self):
        raw = '[{"category": "文体", "insight": ""}, {"category": "キャラ", "insight": "個性的"}]'
        result = _parse_insights(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "キャラ")

    def test_角括弧がないJSONの場合ValueError(self):
        # { のみで [ がないため「見つかりません」エラーになる
        with self.assertRaises(ValueError) as ctx:
            _parse_insights('{"category": "文体", "insight": "XXX"}')
        self.assertIn("見つかりません", str(ctx.exception))

    def test_JSON配列がない場合ValueError(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_insights("知見はありません。")
        self.assertIn("見つかりません", str(ctx.exception))

    def test_不正なJSONの場合ValueError(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_insights("[{invalid json}]")
        self.assertIn("パースに失敗", str(ctx.exception))

    def test_フィードバックに中括弧が含まれても正しく動作する(self):
        # .replace() を使うことで {} を含む insight があっても KeyError にならない
        raw = '[{"category": "文体", "insight": "例: {name}のような表現を使う"}]'
        result = _parse_insights(raw)
        self.assertEqual(len(result), 1)
        self.assertIn("{name}", result[0]["insight"])


class TestCallClaude(unittest.TestCase):
    """_call_claude のリトライ動作テスト。"""

    def setUp(self):
        os.environ["ANTHROPIC_API_KEY"] = "test_api_key"
        _get_client.cache_clear()
        self._config = {"model": "claude-sonnet-4-6", "max_tokens": 512, "temperature": 0.3}

    def tearDown(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        _get_client.cache_clear()

    def _make_client_with_response(self, text: str) -> MagicMock:
        """成功レスポンスを返すモッククライアントを生成する。"""
        mock_content = MagicMock()
        mock_content.text = text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client = MagicMock()
        client.messages.create.return_value = mock_response
        return client

    def test_成功時にテキストを返す(self):
        client = self._make_client_with_response('[{"category": "文体", "insight": "テスト"}]')
        result = _call_claude(client, self._config, "テストフィードバック")
        self.assertIn("文体", result)

    def test_ConnectionErrorでリトライする(self):
        client = MagicMock()
        # 3回失敗後に成功
        mock_content = MagicMock()
        mock_content.text = "成功"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client.messages.create.side_effect = [
            anthropic.APIConnectionError(request=MagicMock()),
            anthropic.APIConnectionError(request=MagicMock()),
            mock_response,
        ]
        with patch("knowledge.time.sleep"):
            result = _call_claude(client, self._config, "テスト")
        self.assertEqual(result, "成功")
        self.assertEqual(client.messages.create.call_count, 3)

    def test_5xxエラーでリトライする(self):
        client = MagicMock()
        mock_content = MagicMock()
        mock_content.text = "成功"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client.messages.create.side_effect = [
            anthropic.APIStatusError("Server Error", response=MagicMock(status_code=503), body={}),
            mock_response,
        ]
        with patch("knowledge.time.sleep"):
            result = _call_claude(client, self._config, "テスト")
        self.assertEqual(result, "成功")

    def test_4xxエラーはリトライしない(self):
        client = MagicMock()
        client.messages.create.side_effect = anthropic.APIStatusError(
            "Unauthorized", response=MagicMock(status_code=401), body={}
        )
        with self.assertRaises(anthropic.APIStatusError):
            _call_claude(client, self._config, "テスト")
        self.assertEqual(client.messages.create.call_count, 1)

    def test_最大リトライ後も失敗で例外を送出する(self):
        client = MagicMock()
        client.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
        with patch("knowledge.time.sleep"):
            with self.assertRaises(anthropic.APIConnectionError):
                _call_claude(client, self._config, "テスト")
        self.assertEqual(client.messages.create.call_count, 4)  # 初回 + 3回リトライ

    def test_リトライの待機時間が指数バックオフ(self):
        client = MagicMock()
        client.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
        with patch("knowledge.time.sleep") as mock_sleep:
            with self.assertRaises(anthropic.APIConnectionError):
                _call_claude(client, self._config, "テスト")
        mock_sleep.assert_has_calls([call(2), call(4), call(8)])


class TestExtractAndSaveKnowledge(unittest.TestCase):
    """extract_and_save_knowledge の統合テスト。"""

    def setUp(self):
        os.environ["ANTHROPIC_API_KEY"] = "test_api_key"
        # db.DB_PATH はモジュール読み込み時に固定されるため直接パッチする
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path_patcher = patch("db.DB_PATH", new=Path(self._tmp.name))
        self._db_path_patcher.start()
        db.init_db()
        # lru_cache をリセットして各テストで設定・クライアントを再生成できるようにする
        _load_knowledge_config.cache_clear()
        _get_client.cache_clear()

    def tearDown(self):
        self._db_path_patcher.stop()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        _get_client.cache_clear()
        self._tmp.close()
        os.unlink(self._tmp.name)

    def _mock_claude(self, response_text: str):
        """_call_claude をモックするコンテキストマネージャを返す。"""
        return patch("knowledge._call_claude", return_value=response_text)

    def test_正常系_知見を抽出してDBに保存する(self):
        response_text = '[{"category": "文体", "insight": "テンポよく書く"}]'
        with self._mock_claude(response_text):
            ids = extract_and_save_knowledge("文体がよかった")
        self.assertEqual(len(ids), 1)
        rows = db.get_knowledge()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "文体")
        self.assertEqual(rows[0]["insight"], "テンポよく書く")

    def test_novel_idが関連づけられる(self):
        # FK制約があるため先に小説を登録してIDを取得する
        novel_id = db.save_novel(
            title="テスト小説", genre="ファンタジー", theme="冒険", content="本文テスト"
        )
        response_text = '[{"category": "キャラ", "insight": "主人公を明確に"}]'
        with self._mock_claude(response_text):
            ids = extract_and_save_knowledge("キャラが好き", novel_id=novel_id)
        rows = db.get_knowledge()
        self.assertEqual(rows[0]["source_novel_id"], novel_id)

    def test_存在しないnovel_idのときValueError(self):
        response_text = '[{"category": "文体", "insight": "テンポよく書く"}]'
        with self._mock_claude(response_text):
            with self.assertRaises(ValueError) as ctx:
                extract_and_save_knowledge("面白かった", novel_id=9999)
        self.assertIn("novel_id=9999", str(ctx.exception))

    def test_複数の知見が単一トランザクションで保存される(self):
        response_text = """[
            {"category": "文体", "insight": "会話文を増やす"},
            {"category": "構成", "insight": "伏線を張る"}
        ]"""
        with self._mock_claude(response_text):
            ids = extract_and_save_knowledge("とても面白かった")
        self.assertEqual(len(ids), 2)
        self.assertLess(ids[0], ids[1])

    def test_フィードバックに中括弧が含まれてもKeyErrorにならない(self):
        response_text = '[{"category": "文体", "insight": "表現を豊かにする"}]'
        with self._mock_claude(response_text):
            ids = extract_and_save_knowledge("このコード {sample} のような描写が良かった")
        self.assertEqual(len(ids), 1)

    def test_抽出結果が0件のとき空リストを返す(self):
        response_text = '[{"category": "その他", "insight": "XXX"}]'
        with self._mock_claude(response_text):
            ids = extract_and_save_knowledge("普通だった")
        self.assertEqual(ids, [])

    def test_feedback_textが空のときValueError(self):
        with self.assertRaises(ValueError) as ctx:
            extract_and_save_knowledge("")
        self.assertIn("空", str(ctx.exception))

    def test_feedback_textが空白のみのときValueError(self):
        with self.assertRaises(ValueError):
            extract_and_save_knowledge("   ")

    def test_APIキー未設定のときEnvironmentError(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        _get_client.cache_clear()
        with self.assertRaises(EnvironmentError) as ctx:
            extract_and_save_knowledge("面白かった")
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))


class TestGetKnowledgeForPrompt(unittest.TestCase):
    """get_knowledge_for_prompt のラッパー動作テスト。"""

    def test_db関数の結果をそのまま返す(self):
        with patch("knowledge.db.get_knowledge_for_prompt", return_value="## 知見\n- テスト") as mock:
            result = get_knowledge_for_prompt()
            mock.assert_called_once()
            self.assertEqual(result, "## 知見\n- テスト")

    def test_知見がない場合は空文字を返す(self):
        with patch("knowledge.db.get_knowledge_for_prompt", return_value=""):
            result = get_knowledge_for_prompt()
            self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
