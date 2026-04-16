"""
知見抽出・保存モジュール。
フィードバック受信時に Claude API でテキストから知見を抽出し、
カテゴリ分類して knowledge テーブルに保存する。

カテゴリ定義:
    文体  — 文章表現・語彙・テンポ・句読点の使い方などの評価
    キャラ — 登場人物の魅力・個性・セリフの自然さなどの評価
    構成  — 場面構成・起承転結・ページ配分などの評価
    ジャンル — ジャンル固有の表現・読者層への適合度などの評価

Claude API リトライ仕様:
    - ネットワーク接続エラー（APIConnectionError）および 5xx サーバーエラーをリトライ
    - 最大3回リトライ（初回含む計4回試行）
    - リトライ間隔は指数バックオフ（2秒 → 4秒 → 8秒）
    - 4xx クライアントエラー（認証失敗・不正リクエスト等）はリトライしない
"""

import functools
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

import db

# モジュール読み込み時に .env を1回だけ読み込む（generator.py と同じパターン）
load_dotenv()

logger = logging.getLogger(__name__)

# 有効なカテゴリ（DBのCHECK制約と合わせる）
VALID_CATEGORIES = frozenset({"文体", "キャラ", "構成", "ジャンル"})

_SETTINGS_DIR = Path(__file__).parent / "settings"
_MODEL_CONFIG_PATH = _SETTINGS_DIR / "model_config.json"

# Claude API リトライ設定
_MAX_RETRIES = 3       # 初回失敗後の最大リトライ回数
_RETRY_BASE_WAIT = 2   # 指数バックオフの基本秒数（2 → 4 → 8 秒）

# 知見抽出プロンプト（{feedback_text} はプレースホルダー。埋め込みは .replace() で行う）
_EXTRACT_PROMPT = """\
あなたは小説創作のフィードバックを分析する専門家です。
以下のフィードバックから、今後の小説生成に活かせる具体的な知見を抽出してください。

## フィードバック
{feedback_text}

## 抽出ルール
- 以下の4カテゴリのうち、フィードバックから読み取れるカテゴリのみ出力してください
  - 文体: 文章表現・語彙・テンポ・句読点の使い方などへの言及
  - キャラ: 登場人物の魅力・個性・セリフの自然さなどへの言及
  - 構成: 場面構成・起承転結・展開のテンポなどへの言及
  - ジャンル: ジャンル固有の表現・読者層への適合度などへの言及
- 言及がないカテゴリは出力しないでください
- 各カテゴリにつき1〜2件に絞り、次回生成時に即活用できる具体的な指針を書いてください
- 否定的フィードバックは「〜を避ける」、肯定的フィードバックは「〜を取り入れる」の形で記述してください

## 出力形式
JSON配列のみ出力してください（前置きや説明は不要）。
[
  {"category": "文体", "insight": "具体的な知見"},
  {"category": "キャラ", "insight": "具体的な知見"}
]
"""


@functools.lru_cache(maxsize=1)
def _load_knowledge_config() -> dict:
    """
    model_config.json から knowledge セクションを読み込む。
    lru_cache により初回のみファイルI/Oを行う。

    Returns:
        knowledge 設定 dict（model / max_tokens / temperature）。
    """
    with open(_MODEL_CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    return {
        "model": os.getenv("CLAUDE_MODEL", config["model"]),
        **config["knowledge"],
    }


@functools.lru_cache(maxsize=1)
def _get_client() -> anthropic.Anthropic:
    """
    Anthropic クライアントを生成してキャッシュする。
    lru_cache により初回のみインスタンスを生成する。

    lru_cache は例外をキャッシュしないため、APIキー未設定で EnvironmentError が発生した場合は
    キャッシュされず、次回呼び出し時に再チェックされる。

    Returns:
        Anthropic クライアントインスタンス。

    Raises:
        EnvironmentError: ANTHROPIC_API_KEY が未設定の場合。
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY が設定されていません。.env ファイルを確認してください。"
        )
    return anthropic.Anthropic(api_key=api_key)


def _call_claude(client: anthropic.Anthropic, config: dict, feedback_text: str) -> str:
    """
    Claude API にフィードバックを渡し、知見JSON文字列を返す。
    ネットワークエラーや 5xx サーバーエラーは指数バックオフでリトライする。

    .replace() でプレースホルダーを埋め込むことで、
    フィードバックに `{}` が含まれても KeyError が発生しない。

    Args:
        client: Anthropic クライアント。
        config: knowledge 設定（model / max_tokens / temperature）。
        feedback_text: 分析対象のフィードバックテキスト。

    Returns:
        Claude が生成した生テキスト。

    Raises:
        anthropic.APIError: 最大リトライ後も失敗した場合、または 4xx エラーの場合。
    """
    prompt = _EXTRACT_PROMPT.replace("{feedback_text}", feedback_text)

    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=config["model"],
                max_tokens=config["max_tokens"],
                temperature=config["temperature"],
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()

        except anthropic.APIConnectionError as exc:
            # ネットワーク接続エラーはリトライ対象
            last_exc = exc
        except anthropic.APIStatusError as exc:
            # 5xx はリトライ対象、4xx は即失敗
            if exc.status_code < 500:
                logger.error(
                    "Claude API クライアントエラー（リトライなし）: status=%d", exc.status_code
                )
                raise
            last_exc = exc

        if attempt < _MAX_RETRIES:
            wait = _RETRY_BASE_WAIT ** (attempt + 1)
            logger.warning(
                "Claude API 呼び出し失敗（試行%d回目）。%d秒後にリトライします。エラー: %s",
                attempt + 1,
                wait,
                last_exc,
            )
            time.sleep(wait)
        else:
            logger.error("Claude API 呼び出し失敗（最大リトライ回数到達）。エラー: %s", last_exc)

    assert last_exc is not None  # ループが1回以上実行された保証（型検査用）
    raise last_exc


def _parse_insights(raw: str) -> list[dict]:
    """
    Claude の出力テキストから知見リストをパースする。

    最初の `[` から最後の `]` までを候補として抽出することで、
    前置き文・コードブロック・greedy マッチ問題を回避する。
    カテゴリが VALID_CATEGORIES に含まれない行は除外する。

    Args:
        raw: Claude が生成した生テキスト。

    Returns:
        {"category": str, "insight": str} のリスト（不正なエントリは除外済み）。

    Raises:
        ValueError: JSON配列が見つからない、またはパースに失敗した場合。
    """
    # 最初の [ から最後の ] を候補とすることで greedy マッチを回避
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or start >= end:
        raise ValueError(f"知見JSONが見つかりませんでした。出力内容: {raw[:200]}")

    try:
        items = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"知見JSONのパースに失敗しました: {e}\n出力内容: {raw[:200]}") from e

    if not isinstance(items, list):
        raise ValueError(f"知見JSONがリスト形式ではありません: {type(items)}")

    # 不正なカテゴリ・空 insight のエントリを除外してログに残す
    valid_items = []
    for item in items:
        category = item.get("category", "")
        insight = item.get("insight", "").strip()
        if category not in VALID_CATEGORIES:
            logger.warning("無効なカテゴリをスキップします: %s", category)
            continue
        if not insight:
            logger.warning("insight が空のエントリをスキップします: %s", item)
            continue
        valid_items.append({"category": category, "insight": insight})

    return valid_items


def extract_and_save_knowledge(
    feedback_text: str,
    novel_id: Optional[int] = None,
) -> list[int]:
    """
    フィードバックテキストから知見を抽出し、knowledge テーブルに保存する。

    Claude API でフィードバックを分析してカテゴリ別の知見リストを生成し、
    全件を単一トランザクションで DB に保存する。

    Args:
        feedback_text: ユーザーが書いたフィードバックテキスト。
        novel_id: フィードバック対象の小説ID（Noneの場合は関連づけなし）。

    Returns:
        保存した knowledge レコードのIDリスト（0件の場合は空リスト）。

    Raises:
        EnvironmentError: ANTHROPIC_API_KEY が未設定の場合。
        ValueError: feedback_text が空の場合、または知見のパースに失敗した場合。
        ValueError: novel_id が novels テーブルに存在しない場合（FK制約違反）。
    """
    if not feedback_text or not feedback_text.strip():
        raise ValueError("feedback_text が空です。フィードバックテキストを指定してください。")

    config = _load_knowledge_config()
    client = _get_client()

    logger.info("知見抽出を開始します（novel_id=%s）", novel_id)
    raw = _call_claude(client, config, feedback_text.strip())
    logger.debug("Claude 出力: %s", raw)

    insights = _parse_insights(raw)
    if not insights:
        logger.warning("抽出された知見が0件でした。フィードバック内容を確認してください。")
        return []

    try:
        saved_ids = db.save_knowledge_bulk(insights, source_novel_id=novel_id)
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"知見の保存に失敗しました。novel_id={novel_id} が novels テーブルに存在しない可能性があります: {e}"
        ) from e

    logger.info("知見抽出完了: %d件保存 (novel_id=%s)", len(saved_ids), novel_id)
    for i, (item, kid) in enumerate(zip(insights, saved_ids)):
        logger.info("  [%d] id=%d category=%s", i + 1, kid, item["category"])

    return saved_ids


def get_knowledge_for_prompt() -> str:
    """
    小説生成プロンプトに組み込む形式で知見を返す。

    db.get_knowledge_for_prompt() のラッパー。
    knowledge モジュールから一元的に知見を取得できるようにする。

    Returns:
        プロンプト埋め込み用の知見テキスト（知見がない場合は空文字）。
    """
    return db.get_knowledge_for_prompt()
