"""
知見抽出・保存モジュール。
フィードバック受信時に Claude API でテキストから知見を抽出し、
カテゴリ分類して knowledge テーブルに保存する。

カテゴリ定義:
    文体  — 文章表現・語彙・テンポ・句読点の使い方などの評価
    キャラ — 登場人物の魅力・個性・セリフの自然さなどの評価
    構成  — 場面構成・起承転結・ページ配分などの評価
    ジャンル — ジャンル固有の表現・読者層への適合度などの評価
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

import db

logger = logging.getLogger(__name__)

# 有効なカテゴリ（DBのCHECK制約と合わせる）
VALID_CATEGORIES = frozenset({"文体", "キャラ", "構成", "ジャンル"})

_SETTINGS_DIR = Path(__file__).parent / "settings"
_MODEL_CONFIG_PATH = _SETTINGS_DIR / "model_config.json"

# 知見抽出プロンプト
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
  {{"category": "文体", "insight": "具体的な知見"}},
  {{"category": "キャラ", "insight": "具体的な知見"}}
]
"""


def _get_client() -> anthropic.Anthropic:
    """
    Anthropic クライアントを生成する。

    Raises:
        EnvironmentError: ANTHROPIC_API_KEY が未設定の場合。
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY が設定されていません。.env ファイルを確認してください。"
        )
    return anthropic.Anthropic(api_key=api_key)


def _load_knowledge_config() -> dict:
    """
    model_config.json から knowledge セクションを読み込む。

    Returns:
        knowledge 設定 dict（model / max_tokens / temperature）。
    """
    with open(_MODEL_CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    return {
        "model": os.getenv("CLAUDE_MODEL", config["model"]),
        **config["knowledge"],
    }


def _call_claude(client: anthropic.Anthropic, config: dict, feedback_text: str) -> str:
    """
    Claude API にフィードバックを渡し、知見JSON文字列を返す。

    Args:
        client: Anthropic クライアント。
        config: knowledge 設定（model / max_tokens / temperature）。
        feedback_text: 分析対象のフィードバックテキスト。

    Returns:
        Claude が生成した生テキスト。
    """
    prompt = _EXTRACT_PROMPT.format(feedback_text=feedback_text)
    response = client.messages.create(
        model=config["model"],
        max_tokens=config["max_tokens"],
        temperature=config["temperature"],
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _parse_insights(raw: str) -> list[dict]:
    """
    Claude の出力テキストから知見リストをパースする。

    前置き文や ```json ブロックが含まれていても最初のJSON配列を抽出する。
    カテゴリが VALID_CATEGORIES に含まれない行は除外する。

    Args:
        raw: Claude が生成した生テキスト。

    Returns:
        {"category": str, "insight": str} のリスト（不正なエントリは除外済み）。

    Raises:
        ValueError: JSON配列が見つからない、またはパースに失敗した場合。
    """
    # JSON配列を抽出（前置きテキストや ```json ブロックを無視）
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise ValueError(f"知見JSONが見つかりませんでした。出力内容: {raw[:200]}")

    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"知見JSONのパースに失敗しました: {e}\n出力内容: {raw[:200]}") from e

    if not isinstance(items, list):
        raise ValueError(f"知見JSONがリスト形式ではありません: {type(items)}")

    # 不正なカテゴリのエントリを除外してログに残す
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
    各知見を db.save_knowledge() でDBに保存する。

    Args:
        feedback_text: ユーザーが書いたフィードバックテキスト。
        novel_id: フィードバック対象の小説ID（Noneの場合は関連づけなし）。

    Returns:
        保存した knowledge レコードのIDリスト（0件の場合は空リスト）。

    Raises:
        EnvironmentError: ANTHROPIC_API_KEY が未設定の場合。
        ValueError: feedback_text が空の場合、または知見のパースに失敗した場合。
    """
    load_dotenv()

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

    saved_ids: list[int] = []
    for item in insights:
        knowledge_id = db.save_knowledge(
            category=item["category"],
            insight=item["insight"],
            source_novel_id=novel_id,
        )
        saved_ids.append(knowledge_id)
        logger.info("知見を保存しました: id=%d category=%s", knowledge_id, item["category"])

    logger.info("知見抽出完了: %d件保存 (novel_id=%s)", len(saved_ids), novel_id)
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
