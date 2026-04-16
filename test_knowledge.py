"""
knowledge.py のユニットテスト。
Claude API と DB への依存をモックして動作検証する。
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import db
from knowledge import (
    VALID_CATEGORIES,
    _parse_insights,
    extract_and_save_knowledge,
    get_knowledge_for_prompt,
)


def _make_claude_response(text: str) -> MagicMock:
    """Claude APIレスポンスのモックを生成する。"""
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


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

    def test_JSON配列がない場合ValueError(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_insights("知見はありません。")
        self.assertIn("見つかりません", str(ctx.exception))

    def test_不正なJSONの場合ValueError(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_insights("[{invalid json}]")
        self.assertIn("パースに失敗", str(ctx.exception))

    def test_リスト以外のJSONの場合ValueError(self):
        # JSON配列ではなくオブジェクトのみだと配列が見つからずエラーになる
        with self.assertRaises(ValueError) as ctx:
            _parse_insights('{"category": "文体", "insight": "XXX"}')
        self.assertIn("見つかりません", str(ctx.exception))


class TestExtractAndSaveKnowledge(unittest.TestCase):
    """extract_and_save_knowledge の統合テスト。"""

    def setUp(self):
        os.environ["ANTHROPIC_API_KEY"] = "test_api_key"
        # db.DB_PATH はモジュール読み込み時に固定されるため直接パッチする
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path_patcher = patch("db.DB_PATH", new=__import__("pathlib").Path(self._tmp.name))
        self._db_path_patcher.start()
        db.init_db()

    def tearDown(self):
        self._db_path_patcher.stop()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self._tmp.close()
        os.unlink(self._tmp.name)

    def _mock_claude(self, response_text: str):
        """_call_claude をモックするコンテキストマネージャを返す。"""
        return patch(
            "knowledge._call_claude",
            return_value=response_text,
        )

    def test_正常系_知見を抽出してDBに保存する(self):
        response_text = '[{"category": "文体", "insight": "テンポよく書く"}]'
        with self._mock_claude(response_text):
            ids = extract_and_save_knowledge("文体がよかった")
        self.assertEqual(len(ids), 1)
        # DBに保存されたか確認
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

    def test_複数の知見が保存される(self):
        response_text = """[
            {"category": "文体", "insight": "会話文を増やす"},
            {"category": "構成", "insight": "伏線を張る"}
        ]"""
        with self._mock_claude(response_text):
            ids = extract_and_save_knowledge("とても面白かった")
        self.assertEqual(len(ids), 2)

    def test_抽出結果が0件のとき空リストを返す(self):
        # 無効カテゴリのみで実際の保存は0件
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
