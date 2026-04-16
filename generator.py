"""
小説生成モジュール。
Claude APIを使って2段階生成（構成案 → 場面ごとの本文）で短編小説を生成する。

生成フロー:
    Stage 1: ジャンル・テーマ・知見をもとに構成案（JSON）を生成
    Stage 2: 構成案の各場面を順番に生成し、前場面の本文を引き継いで矛盾を防ぐ
    最終: 全場面を結合して完成原稿をDBに保存
"""

import json
import os
import random
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

import db

load_dotenv()

# ──────────────────────────────────────────────
# 設定読み込み
# ──────────────────────────────────────────────

_SETTINGS_DIR = Path(__file__).parent / "settings"
_BASE_PROMPT_PATH = _SETTINGS_DIR / "base_prompt.md"
_MODEL_CONFIG_PATH = _SETTINGS_DIR / "model_config.json"


def _load_model_config() -> dict:
    """model_config.json を読み込む。"""
    with open(_MODEL_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_base_prompt() -> str:
    """base_prompt.md を読み込む。"""
    with open(_BASE_PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


def _get_client() -> anthropic.Anthropic:
    """Anthropic クライアントを生成する。"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY が設定されていません。.env を確認してください。")
    return anthropic.Anthropic(api_key=api_key)


# ──────────────────────────────────────────────
# ジャンル・テーマ選択
# ──────────────────────────────────────────────

def pick_genre_and_theme(genre_name: Optional[str] = None, theme: Optional[str] = None) -> tuple[str, str]:
    """
    ジャンルとテーマを決定して返す。

    引数で指定された場合はそれを優先する。
    指定がない場合は genre_settings テーブルの重みに従ってランダム選択する。

    Args:
        genre_name: 指定ジャンル名（Noneの場合はランダム）
        theme: 指定テーマ（Noneの場合はランダム）

    Returns:
        (genre_name, theme) のタプル

    Raises:
        ValueError: 有効なジャンルが1件もない場合
    """
    genres = db.get_genre_settings(active_only=True)
    if not genres:
        raise ValueError("有効なジャンルが登録されていません。genre_settings を確認してください。")

    # ジャンル決定
    if genre_name:
        # 指定ジャンルがDBに存在するか確認（存在しなければそのまま使用）
        selected_genre = next((g for g in genres if g["name"] == genre_name), None)
        if not selected_genre:
            selected_genre = {"name": genre_name, "sub_themes": []}
    else:
        # 重み付きランダム選択
        weights = [g["weight"] for g in genres]
        selected_genre = random.choices(genres, weights=weights, k=1)[0]

    # テーマ決定
    if theme:
        selected_theme = theme
    elif selected_genre.get("sub_themes"):
        selected_theme = random.choice(selected_genre["sub_themes"])
    else:
        selected_theme = "オリジナル"

    return selected_genre["name"], selected_theme


# ──────────────────────────────────────────────
# Stage 1: 構成案生成
# ──────────────────────────────────────────────

def _generate_outline(
    client: anthropic.Anthropic,
    model: str,
    genre: str,
    theme: str,
    knowledge_text: str,
    config: dict,
) -> dict:
    """
    Stage 1: ジャンル・テーマをもとに構成案をJSON形式で生成する。
    JSONパースに失敗した場合は最大3回リトライする。

    Args:
        client: Anthropic クライアント
        model: 使用モデルID
        genre: ジャンル
        theme: テーマ
        knowledge_text: 過去の知見テキスト
        config: model_config の stage1 設定

    Returns:
        構成案のdict（title / characters / scenes / total_target_chars）

    Raises:
        RuntimeError: 最大リトライ回数を超えてもJSON生成に失敗した場合
    """
    base_prompt = _load_base_prompt()

    # Stage 1 プロンプトを base_prompt.md から抽出（--- Stage 1 --- 〜 --- Stage 2 --- の間）
    stage1_section = base_prompt.split("## Stage 1 - 構成案生成プロンプト")[1].split("## Stage 2 -")[0].strip()

    prompt = f"""{stage1_section}

{knowledge_text}

## 今回の生成条件
- ジャンル: {genre}
- テーマ: {theme}

上記の条件で短編小説の構成案をJSON形式で出力してください。JSON以外のテキストは含めないでください。"""

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        response = client.messages.create(
            model=model,
            max_tokens=config["max_tokens"],
            temperature=config["temperature"],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # コードブロック（```json ... ```）で囲まれている場合は除去
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            outline = json.loads(raw)
            # 必須キーの検証
            required_keys = {"title", "characters", "scenes", "total_target_chars"}
            if not required_keys.issubset(outline.keys()):
                raise ValueError(f"必須キーが不足しています: {required_keys - outline.keys()}")
            return outline
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == max_retries:
                raise RuntimeError(
                    f"構成案のJSON生成に{max_retries}回失敗しました。最後のエラー: {e}\n出力内容: {raw[:200]}"
                )
            # リトライ時はプロンプトにエラーを追記して再挑戦
            prompt += f"\n\n（前回の出力がJSON形式ではありませんでした。必ずJSON形式のみ出力してください。）"

    raise RuntimeError("到達しないはずのコードパスです")


# ──────────────────────────────────────────────
# Stage 2: 場面ごとの本文生成
# ──────────────────────────────────────────────

def _generate_scene(
    client: anthropic.Anthropic,
    model: str,
    outline: dict,
    scene: dict,
    previous_text: str,
    config: dict,
) -> str:
    """
    Stage 2: 1場面分の本文を生成する。

    Args:
        client: Anthropic クライアント
        model: 使用モデルID
        outline: Stage 1 で生成した構成案全体
        scene: 今回生成する場面の情報（scene_number / title / summary / target_chars）
        previous_text: 直前場面までの本文（冒頭場面の場合は空文字）
        config: model_config の stage2 設定

    Returns:
        生成された場面の本文テキスト
    """
    base_prompt = _load_base_prompt()
    stage2_section = base_prompt.split("## Stage 2 - 本文生成プロンプト（1場面ごとに使用）")[1].strip()

    # キャラクター情報を整形
    characters_text = "\n".join(
        f"- {c['name']}（{c['role']}）: {c['description']}"
        for c in outline.get("characters", [])
    )

    # 全場面のあらすじを整形
    scenes_summary = "\n".join(
        f"  場面{s['scene_number']}: {s['title']} — {s['summary']}"
        for s in outline.get("scenes", [])
    )

    prompt = f"""{stage2_section}

## 全体構成案
タイトル: {outline['title']}

### 登場人物
{characters_text}

### 全場面のあらすじ
{scenes_summary}

## 直前場面までの本文
{previous_text if previous_text else "（冒頭場面のため、前の本文はありません）"}

## 今回執筆する場面
- 場面番号: {scene['scene_number']} / {len(outline['scenes'])}
- 場面タイトル: {scene['title']}
- あらすじ: {scene['summary']}
- 目標文字数: 約{scene['target_chars']}字

上記の情報をもとに、この場面の本文のみを執筆してください。"""

    response = client.messages.create(
        model=model,
        max_tokens=config["max_tokens"],
        temperature=config["temperature"],
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# ──────────────────────────────────────────────
# メイン生成関数
# ──────────────────────────────────────────────

def generate_novel(
    genre_name: Optional[str] = None,
    theme: Optional[str] = None,
) -> dict:
    """
    短編小説を2段階生成してDBに保存する。

    Args:
        genre_name: ジャンル名（Noneの場合は重み付きランダム選択）
        theme: テーマ（Noneの場合はサブテーマからランダム選択）

    Returns:
        保存した小説のメタデータ dict
        {
            "id": int,
            "title": str,
            "genre": str,
            "theme": str,
            "word_count": int,
        }

    Raises:
        RuntimeError: 構成案のJSON生成に繰り返し失敗した場合
        EnvironmentError: APIキーが未設定の場合
    """
    model_config = _load_model_config()
    # 環境変数でモデルをオーバーライド可能
    model = os.getenv("CLAUDE_MODEL", model_config["model"])

    client = _get_client()

    # ジャンル・テーマ決定
    genre, theme_selected = pick_genre_and_theme(genre_name, theme)

    # 知見をプロンプトに組み込む
    knowledge_text = db.get_knowledge_for_prompt()

    print(f"[Stage 1] 構成案を生成中... ジャンル={genre} / テーマ={theme_selected}")
    outline = _generate_outline(
        client, model, genre, theme_selected, knowledge_text, model_config["stage1"]
    )
    title = outline["title"]
    scene_count = len(outline["scenes"])
    print(f"[Stage 1] 完了: タイトル=「{title}」 / {scene_count}場面構成")

    # Stage 2: 場面ごとに本文生成
    all_text_parts: list[str] = []
    previous_text = ""

    for i, scene in enumerate(outline["scenes"], start=1):
        print(f"[Stage 2] 場面 {i}/{scene_count} を生成中: {scene['title']}")
        scene_text = _generate_scene(
            client, model, outline, scene, previous_text, model_config["stage2"]
        )
        all_text_parts.append(scene_text)
        # 次の場面生成のために直前テキストを更新（長すぎる場合は最後の1,500字のみ渡す）
        previous_text = "\n\n".join(all_text_parts)
        if len(previous_text) > 1500:
            previous_text = "（省略）\n\n" + previous_text[-1500:]

    # 全場面を結合
    full_content = "\n\n".join(all_text_parts)
    print(f"[Stage 2] 完了: 総文字数={len(full_content)}字")

    # DBに保存
    novel_id = db.save_novel(
        title=title,
        genre=genre,
        theme=theme_selected,
        content=full_content,
    )

    print(f"[保存完了] novel_id={novel_id}")

    return {
        "id": novel_id,
        "title": title,
        "genre": genre,
        "theme": theme_selected,
        "word_count": len(full_content),
    }
