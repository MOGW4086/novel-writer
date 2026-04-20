"""
実行エントリーポイント。
タスクスケジューラからの自動実行と、CLI からの手動実行の両方に対応する。

使い方:
    # 自動実行（タスクスケジューラから）
    python main.py

    # 手動実行
    python main.py --manual
    python main.py --manual --genre "異世界転生" --theme "勇者召喚からの逃走"
    python main.py --manual --series "魔法少女クロニクル"
    python main.py --manual --series "魔法少女クロニクル" --series-description "魔法少女たちの戦い"

    # シリーズ一覧表示
    python main.py --list-series
"""

import argparse
import logging
import sys
import unicodedata

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
    parser.add_argument(
        "--series",
        type=str,
        default=None,
        help="所属シリーズ名。既存シリーズ名を指定すると追加、新規名を指定すると作成。省略時はシリーズなし。",
    )
    parser.add_argument(
        "--series-description",
        type=str,
        default="",
        help="新規シリーズを作成する場合の説明文（--series と併用）。",
    )
    parser.add_argument(
        "--list-series",
        action="store_true",
        help="登録済みシリーズの一覧を表示して終了する。",
    )
    return parser.parse_args()


def _display_width(text: str) -> int:
    """
    端末での表示幅を返す。全角文字は2、半角文字は1として計算する。
    f-string の :<N パディングは文字数基準のため、日本語混じりテキストの
    列揃えには unicodedata.east_asian_width による幅計算が必要。
    """
    width = 0
    for ch in text:
        eaw = unicodedata.east_asian_width(ch)
        width += 2 if eaw in ("W", "F") else 1
    return width


def _ljust_display(text: str, width: int) -> str:
    """表示幅を考慮して左詰めパディングした文字列を返す。"""
    padding = width - _display_width(text)
    return text + " " * max(padding, 0)


def _truncate_display(text: str, width: int) -> str:
    """表示幅を超える場合は末尾を … で切り詰める。"""
    if _display_width(text) <= width:
        return text
    result = ""
    current = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if current + w > width - 1:
            break
        result += ch
        current += w
    return result + "…"


def _list_series() -> None:
    """
    登録済みシリーズの一覧を標準出力に表示する。
    DB接続失敗時はエラーメッセージを標準エラー出力に表示して終了する。
    """
    try:
        series_list = db.get_series_list()
    except Exception as e:
        print(f"シリーズ一覧の取得に失敗しました: {e}", file=sys.stderr)
        sys.exit(1)

    if not series_list:
        print("シリーズはまだ登録されていません。")
        return

    title_width = 30
    print(f"{'ID':>4}  {_ljust_display('タイトル', title_width)}  {_ljust_display('話数', 4)}  {_ljust_display('未読', 4)}  {_ljust_display('最終更新', 10)}")
    print("-" * (4 + 2 + title_width + 2 + 4 + 2 + 4 + 2 + 10))
    for s in series_list:
        latest = (s.get("latest_generated_at") or "")[:10]
        title = _ljust_display(_truncate_display(s["title"], title_width), title_width)
        print(
            f"{s['id']:>4}  {title}  "
            f"{s.get('novel_count', 0):>2}話  "
            f"{s.get('unread_count', 0):>4}  "
            f"{latest}"
        )


def _run(genre: str | None, theme: str | None, series: str | None, series_description: str) -> None:
    """
    小説生成 → LINE通知 の一連の処理を実行する。

    Args:
        genre: ジャンル名（Noneの場合はランダム選択）。
        theme: テーマ（Noneの場合はランダム選択）。
        series: シリーズ名（Noneの場合はシリーズなし）。
        series_description: 新規シリーズ作成時の説明文。

    Raises:
        Exception: 生成で回復不能なエラーが発生した場合。
    """
    # シリーズID解決（指定があれば取得または新規作成）
    series_id = None
    if series:
        series_id = db.get_or_create_series(series, series_description)
        logger.info("シリーズ決定: id=%d title=%r", series_id, series)

    # 小説生成
    logger.info("小説生成を開始します（genre=%s / theme=%s / series_id=%s）", genre, theme, series_id)
    novel_meta = generator.generate_novel(genre_name=genre, theme=theme, series_id=series_id)
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
        series_name=series,
        episode_number=novel_meta["episode_number"],
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

    # DB初期化（テーブルが未作成の場合のみ CREATE TABLE を実行）
    db.init_db()

    # --list-series は一覧表示のみで終了
    if args.list_series:
        _list_series()
        return

    mode = "手動" if args.manual else "自動（スケジューラ）"
    logger.info("=== novel-writer 起動 [%s実行] ===", mode)
    logger.info("DB初期化完了")

    # --genre / --theme / --series は --manual なしでも受け付けるが、意味を持つのは --manual 時のみ
    genre = args.genre if args.manual else None
    theme = args.theme if args.manual else None
    series = args.series if args.manual else None
    series_description = args.series_description if args.manual else ""

    try:
        _run(genre=genre, theme=theme, series=series, series_description=series_description)
    except Exception:
        logger.exception("実行中に予期しないエラーが発生しました")
        sys.exit(1)

    logger.info("=== novel-writer 正常終了 ===")


if __name__ == "__main__":
    main()
