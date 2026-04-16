"""
実行エントリーポイント。
タスクスケジューラからの自動実行と、CLI からの手動実行の両方に対応する。

使い方:
    # 自動実行（タスクスケジューラから）
    python main.py

    # 手動実行
    python main.py --manual
    python main.py --manual --genre "異世界転生" --theme "勇者召喚からの逃走"
"""

import argparse
import logging
import sys

import db
import generator
import notifier

# モジュールレベルではロガーの取得のみ行い、basicConfig は main() 内で設定する。
# モジュールレベルで basicConfig を呼び出すと、テストや他モジュールからのインポート時に
# グローバルなロギング設定を意図せず変更・上書きしてしまうため。
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    """
    CLIの引数を解析して返す。

    Returns:
        解析済みのNamespaceオブジェクト。
    """
    parser = argparse.ArgumentParser(
        description="novel-writer: 短編小説自動生成ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python main.py                                        # 自動実行（スケジューラから）
  python main.py --manual                               # ランダムジャンル・テーマで手動実行
  python main.py --manual --genre "異世界転生"          # ジャンル指定
  python main.py --manual --genre "異世界転生" --theme "勇者召喚からの逃走"
        """,
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="手動実行モード。指定しない場合はスケジューラからの自動実行扱い。",
    )
    parser.add_argument(
        "--genre",
        type=str,
        default=None,
        help="生成するジャンル名（--manual 指定時のみ有効）。省略時はランダム選択。",
    )
    parser.add_argument(
        "--theme",
        type=str,
        default=None,
        help="生成するテーマ（--manual 指定時のみ有効）。省略時はランダム選択。",
    )
    return parser.parse_args()


def _run(genre: str | None, theme: str | None) -> None:
    """
    小説生成 → LINE通知 の一連の処理を実行する。

    Args:
        genre: ジャンル名（Noneの場合はランダム選択）。
        theme: テーマ（Noneの場合はランダム選択）。

    Raises:
        Exception: 生成で回復不能なエラーが発生した場合。
    """
    # 小説生成
    logger.info("小説生成を開始します（genre=%s / theme=%s）", genre, theme)
    novel_meta = generator.generate_novel(genre_name=genre, theme=theme)
    logger.info(
        "小説生成完了: id=%d title=%r genre=%s theme=%s word_count=%d",
        novel_meta["id"],
        novel_meta["title"],
        novel_meta["genre"],
        novel_meta["theme"],
        novel_meta["word_count"],
    )

    # LINE通知
    payload = notifier.NovelNotifyPayload(
        title=novel_meta["title"],
        genre=novel_meta["genre"],
        theme=novel_meta["theme"],
        char_count=novel_meta["word_count"],
    )
    try:
        notifier.send_novel_notification(payload)
        logger.info("LINE通知を送信しました")
    except Exception:
        # 通知失敗は生成済みコンテンツへの影響がないため、スタックトレース付きで記録して続行
        logger.exception("LINE通知に失敗しました（生成データは保存済み）")


def main() -> None:
    """
    エントリーポイント。
    引数を解析し、DB初期化後に小説生成・通知を実行する。
    """
    # ロギング設定（タイムスタンプ付きで標準出力へ）
    # main() 内で設定することでテスト・インポート時への副作用を防ぐ
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    args = _parse_args()

    mode = "手動" if args.manual else "自動（スケジューラ）"
    logger.info("=== novel-writer 起動 [%s実行] ===", mode)

    # DB初期化（テーブルが未作成の場合のみ CREATE TABLE を実行）
    db.init_db()
    logger.info("DB初期化完了")

    # --genre / --theme は --manual なしでも受け付けるが、意味を持つのは --manual 時のみ
    genre = args.genre if args.manual else None
    theme = args.theme if args.manual else None

    try:
        _run(genre=genre, theme=theme)
    except Exception:
        logger.exception("実行中に予期しないエラーが発生しました")
        sys.exit(1)

    logger.info("=== novel-writer 正常終了 ===")


if __name__ == "__main__":
    main()
