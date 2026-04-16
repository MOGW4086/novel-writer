"""
main.py のユニットテスト。
generator / notifier / db をモックして動作を検証する。
"""

import sys
import unittest
from unittest.mock import patch

import requests

import main


def _make_novel_meta(**kwargs) -> dict:
    """テスト用小説メタデータを生成する。"""
    defaults = dict(id=1, title="テスト小説", genre="ファンタジー", theme="冒険", word_count=3000)
    defaults.update(kwargs)
    return defaults


class TestParseArgs(unittest.TestCase):
    """_parse_args のCLI引数解析テスト。"""

    def _parse(self, argv: list) -> object:
        """sys.argv を一時的に置き換えて引数をパースする。"""
        with patch.object(sys, "argv", ["main.py"] + argv):
            return main._parse_args()

    def test_引数なしでmanualがFalse(self):
        args = self._parse([])
        self.assertFalse(args.manual)
        self.assertIsNone(args.genre)
        self.assertIsNone(args.theme)

    def test_manualフラグを認識する(self):
        args = self._parse(["--manual"])
        self.assertTrue(args.manual)

    def test_genre引数を認識する(self):
        args = self._parse(["--manual", "--genre", "異世界転生"])
        self.assertEqual(args.genre, "異世界転生")

    def test_themeとgenreを同時に指定できる(self):
        args = self._parse(["--manual", "--genre", "異世界転生", "--theme", "勇者召喚"])
        self.assertEqual(args.genre, "異世界転生")
        self.assertEqual(args.theme, "勇者召喚")


class TestRun(unittest.TestCase):
    """_run の生成・通知フロー統合テスト。"""

    def test_生成後にLINE通知を送信する(self):
        novel_meta = _make_novel_meta()
        with patch("main.generator.generate_novel", return_value=novel_meta) as mock_gen:
            with patch("main.notifier.send_novel_notification") as mock_notify:
                main._run(genre="ファンタジー", theme="冒険")
                mock_gen.assert_called_once_with(genre_name="ファンタジー", theme="冒険")
                mock_notify.assert_called_once()

    def test_通知失敗でも例外を送出しない(self):
        novel_meta = _make_novel_meta()
        with patch("main.generator.generate_novel", return_value=novel_meta):
            with patch(
                "main.notifier.send_novel_notification",
                side_effect=requests.HTTPError("通知失敗"),
            ):
                # 例外なく終了すれば成功
                main._run(genre=None, theme=None)

    def test_通知ペイロードに正しい値が渡される(self):
        novel_meta = _make_novel_meta(title="テスト", genre="SF", theme="宇宙", word_count=5000)
        captured = {}

        def fake_notify(payload):
            captured["payload"] = payload

        with patch("main.generator.generate_novel", return_value=novel_meta):
            with patch("main.notifier.send_novel_notification", side_effect=fake_notify):
                main._run(genre=None, theme=None)

        p = captured["payload"]
        self.assertEqual(p.title, "テスト")
        self.assertEqual(p.genre, "SF")
        self.assertEqual(p.theme, "宇宙")
        self.assertEqual(p.char_count, 5000)

    def test_生成失敗で例外を送出する(self):
        with patch(
            "main.generator.generate_novel",
            side_effect=RuntimeError("生成失敗"),
        ):
            with self.assertRaises(RuntimeError):
                main._run(genre=None, theme=None)


class TestMain(unittest.TestCase):
    """main() の統合フローテスト。"""

    def _run_main(self, argv: list) -> None:
        """sys.argv を差し替えて main() を実行する。"""
        with patch.object(sys, "argv", ["main.py"] + argv):
            main.main()

    def test_自動実行モードで生成が呼ばれる(self):
        with patch("main.db.init_db"):
            with patch("main.generator.generate_novel", return_value=_make_novel_meta()) as mock_gen:
                with patch("main.notifier.send_novel_notification"):
                    self._run_main([])
                    # --manual なし: genre/theme は None
                    mock_gen.assert_called_once_with(genre_name=None, theme=None)

    def test_手動実行でgenreとthemeが渡される(self):
        with patch("main.db.init_db"):
            with patch(
                "main.generator.generate_novel", return_value=_make_novel_meta()
            ) as mock_gen:
                with patch("main.notifier.send_novel_notification"):
                    self._run_main(["--manual", "--genre", "異世界転生", "--theme", "勇者"])
                    mock_gen.assert_called_once_with(genre_name="異世界転生", theme="勇者")

    def test_manualなしでgenreを渡してもNoneで呼ばれる(self):
        # --genre だけ指定しても --manual なしなら無視される
        with patch("main.db.init_db"):
            with patch(
                "main.generator.generate_novel", return_value=_make_novel_meta()
            ) as mock_gen:
                with patch("main.notifier.send_novel_notification"):
                    self._run_main(["--genre", "異世界転生"])
                    mock_gen.assert_called_once_with(genre_name=None, theme=None)

    def test_生成エラーでsys_exitを呼ぶ(self):
        with patch("main.db.init_db"):
            with patch(
                "main.generator.generate_novel",
                side_effect=RuntimeError("生成失敗"),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    self._run_main([])
                self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
