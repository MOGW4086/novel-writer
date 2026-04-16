"""
データベース操作モジュール。
SQLiteを使用して小説・キャラクター・フィードバック・知見・ジャンル設定を管理する。
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# DBファイルのパス（環境変数 DB_PATH でオーバーライド可能）
# 注意: SQLite の :memory: はコネクションごとに別DBになるため、
#       このモジュールの接続都度生成パターンでは使用不可。テストには一時ファイルを使うこと。
DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).parent / "data" / "novels.db")))


@contextmanager
def get_connection():
    """DBコネクションのコンテキストマネージャ。自動でコミット/ロールバックを行う。"""
    if str(DB_PATH) != ":memory:":
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")  # 外部キー制約を有効化（SQLiteはデフォルト無効）
    conn.row_factory = sqlite3.Row  # カラム名でアクセスできるようにする
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ──────────────────────────────────────────────
# テーブル初期化
# ──────────────────────────────────────────────

def init_db() -> None:
    """全テーブルを作成する。既に存在する場合はスキップ。"""
    with get_connection() as conn:
        conn.executescript("""
            -- 小説本文・メタデータ
            CREATE TABLE IF NOT EXISTS novels (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT    NOT NULL,
                genre         TEXT    NOT NULL,
                theme         TEXT    NOT NULL,
                content       TEXT    NOT NULL,
                word_count    INTEGER NOT NULL DEFAULT 0,
                generated_at  TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published'))
            );

            -- 汎用キャラクタープール
            CREATE TABLE IF NOT EXISTS characters (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                age          TEXT,
                appearance   TEXT,
                personality  TEXT,
                background   TEXT,
                abilities    TEXT,
                speech_style TEXT,
                notes        TEXT,
                created_at   TEXT NOT NULL
            );

            -- 小説↔キャラ中間テーブル
            CREATE TABLE IF NOT EXISTS novel_characters (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id         INTEGER NOT NULL REFERENCES novels(id),
                character_id     INTEGER NOT NULL REFERENCES characters(id),
                role             TEXT NOT NULL,
                character_state  TEXT
            );

            -- フィードバック
            CREATE TABLE IF NOT EXISTS feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id   INTEGER NOT NULL REFERENCES novels(id),
                rating     INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                comment    TEXT,
                created_at TEXT NOT NULL
            );

            -- 蓄積知見
            CREATE TABLE IF NOT EXISTS knowledge (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT NOT NULL,
                insight         TEXT NOT NULL,
                source_novel_id INTEGER REFERENCES novels(id),
                created_at      TEXT NOT NULL
            );

            -- ジャンル・テーマ設定
            CREATE TABLE IF NOT EXISTS genre_settings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                weight      INTEGER NOT NULL DEFAULT 5,
                sub_themes  TEXT NOT NULL DEFAULT '[]',
                active      INTEGER NOT NULL DEFAULT 1
            );
        """)


# ──────────────────────────────────────────────
# novels テーブル
# ──────────────────────────────────────────────

def save_novel(
    title: str,
    genre: str,
    theme: str,
    content: str,
    status: str = "draft"
) -> int:
    """
    小説をDBに保存して採番されたIDを返す。

    Args:
        title: タイトル
        genre: ジャンル
        theme: テーマ
        content: 本文全文
        status: 状態（draft / published）

    Returns:
        新規レコードのID
    """
    word_count = len(content)
    generated_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO novels (title, genre, theme, content, word_count, generated_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (title, genre, theme, content, word_count, generated_at, status),
        )
        return cur.lastrowid


def get_novel(novel_id: int) -> Optional[dict]:
    """
    指定IDの小説を取得する。

    Args:
        novel_id: 小説ID

    Returns:
        小説データのdict、存在しない場合はNone
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM novels WHERE id = ?", (novel_id,)
        ).fetchone()
        return dict(row) if row else None


def get_novels(limit: int = 50, offset: int = 0) -> list[dict]:
    """
    小説一覧を生成日降順で取得する。

    Args:
        limit: 取得件数上限
        offset: 取得開始位置

    Returns:
        小説データのdictリスト
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM novels ORDER BY generated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def get_novels_without_feedback() -> list[dict]:
    """
    フィードバックが1件もない小説一覧を取得する。
    1件でもフィードバックが存在する小説は対象外とする。
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT n.* FROM novels n
            LEFT JOIN feedback f ON n.id = f.novel_id
            WHERE f.id IS NULL
            ORDER BY n.generated_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# characters テーブル
# ──────────────────────────────────────────────

def save_character(
    name: str,
    age: str = "",
    appearance: str = "",
    personality: str = "",
    background: str = "",
    abilities: str = "",
    speech_style: str = "",
    notes: str = "",
) -> int:
    """
    キャラクターをDBに保存して採番されたIDを返す。

    Returns:
        新規レコードのID
    """
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO characters
              (name, age, appearance, personality, background, abilities, speech_style, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, age, appearance, personality, background, abilities, speech_style, notes, created_at),
        )
        return cur.lastrowid


def get_characters(limit: int = 50, offset: int = 0) -> list[dict]:
    """
    キャラクター一覧を取得する。

    Args:
        limit: 取得件数上限
        offset: 取得開始位置

    Returns:
        キャラクターデータのdictリスト
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM characters ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def get_character(character_id: int) -> Optional[dict]:
    """指定IDのキャラクターを取得する。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE id = ?", (character_id,)
        ).fetchone()
        return dict(row) if row else None


def update_character(character_id: int, **fields) -> None:
    """
    指定IDのキャラクター情報を更新する。

    Args:
        character_id: 更新対象のキャラクターID
        **fields: 更新するカラム名と値のキーワード引数
    """
    allowed = {"name", "age", "appearance", "personality", "background", "abilities", "speech_style", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [character_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE characters SET {set_clause} WHERE id = ?", values
        )


def save_novel_character(
    novel_id: int,
    character_id: int,
    role: str,
    character_state: Optional[dict] = None,
) -> None:
    """
    小説とキャラクターの関連を保存する。

    Args:
        novel_id: 小説ID
        character_id: キャラクターID
        role: 役割（主人公 / ヒロイン / サブ / モブ）
        character_state: この話でのキャラの状態変化（dict）
    """
    state_json = json.dumps(character_state, ensure_ascii=False) if character_state else None
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO novel_characters (novel_id, character_id, role, character_state)
            VALUES (?, ?, ?, ?)
            """,
            (novel_id, character_id, role, state_json),
        )


def get_novel_characters(novel_id: int) -> list[dict]:
    """
    指定小説に登場するキャラクター一覧をキャラ情報込みで取得する。

    Returns:
        キャラクター情報＋役割＋状態変化のdictリスト
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*, nc.role, nc.character_state
            FROM novel_characters nc
            JOIN characters c ON nc.character_id = c.id
            WHERE nc.novel_id = ?
            """,
            (novel_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("character_state"):
                d["character_state"] = json.loads(d["character_state"])
            result.append(d)
        return result


# ──────────────────────────────────────────────
# feedback テーブル
# ──────────────────────────────────────────────

def save_feedback(novel_id: int, rating: int, comment: str = "") -> int:
    """
    フィードバックを保存して採番されたIDを返す。

    Args:
        novel_id: 対象小説ID
        rating: 評価（1〜5）
        comment: コメント本文

    Returns:
        新規レコードのID

    Raises:
        ValueError: ratingが1〜5の範囲外の場合
    """
    if not 1 <= rating <= 5:
        raise ValueError(f"ratingは1〜5の整数で指定してください: {rating}")
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO feedback (novel_id, rating, comment, created_at) VALUES (?, ?, ?, ?)",
            (novel_id, rating, comment, created_at),
        )
        return cur.lastrowid


def get_feedback(novel_id: int) -> list[dict]:
    """指定小説のフィードバック一覧を取得する。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE novel_id = ? ORDER BY created_at DESC",
            (novel_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# knowledge テーブル
# ──────────────────────────────────────────────

def save_knowledge(
    category: str,
    insight: str,
    source_novel_id: Optional[int] = None,
) -> int:
    """
    知見を保存して採番されたIDを返す。

    Args:
        category: カテゴリ（文体 / キャラ / 構成 / ジャンル）
        insight: 学んだこと
        source_novel_id: 知見の元となった小説ID

    Returns:
        新規レコードのID
    """
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO knowledge (category, insight, source_novel_id, created_at) VALUES (?, ?, ?, ?)",
            (category, insight, source_novel_id, created_at),
        )
        return cur.lastrowid


def get_knowledge(category: Optional[str] = None) -> list[dict]:
    """
    知見一覧を取得する。

    Args:
        category: 指定した場合そのカテゴリのみ取得、Noneの場合は全件

    Returns:
        知見データのdictリスト
    """
    with get_connection() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM knowledge WHERE category = ? ORDER BY created_at DESC",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM knowledge ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_knowledge_for_prompt() -> str:
    """
    小説生成プロンプトに組み込む形式で知見を整形して返す。
    カテゴリ別にまとめ、最新20件を対象とする。

    Returns:
        プロンプト埋め込み用の知見テキスト
    """
    with get_connection() as conn:
        # with ブロック内で dict に変換してコネクション外でも安全に扱えるようにする
        rows = [dict(r) for r in conn.execute(
            "SELECT category, insight FROM knowledge ORDER BY created_at DESC LIMIT 20"
        ).fetchall()]

    if not rows:
        return ""

    # カテゴリ別にまとめる
    by_category: dict[str, list[str]] = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row["insight"])

    lines = ["## 過去のフィードバックから得た知見（必ず参考にすること）"]
    for category, insights in by_category.items():
        lines.append(f"\n### {category}")
        for insight in insights:
            lines.append(f"- {insight}")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# genre_settings テーブル
# ──────────────────────────────────────────────

def load_genre_settings_from_json(genre_config_path: str) -> None:
    """
    genre_config.json からジャンル設定をDBに初期投入する。
    既に同名のジャンルが存在する場合はスキップ（INSERT OR IGNORE）。

    Args:
        genre_config_path: genre_config.json のファイルパス
    """
    with open(genre_config_path, encoding="utf-8") as f:
        config = json.load(f)

    records = [
        (
            genre["name"],
            genre.get("description", ""),
            genre.get("weight", 5),
            json.dumps(genre.get("sub_themes", []), ensure_ascii=False),
            1 if genre.get("active", True) else 0,
        )
        for genre in config["genres"]
    ]

    with get_connection() as conn:
        # INSERT OR IGNORE で重複スキップ・N+1クエリを排除
        conn.executemany(
            """
            INSERT OR IGNORE INTO genre_settings (name, description, weight, sub_themes, active)
            VALUES (?, ?, ?, ?, ?)
            """,
            records,
        )


def get_genre_settings(active_only: bool = True) -> list[dict]:
    """
    ジャンル設定一覧を取得する。

    Args:
        active_only: Trueの場合は有効なジャンルのみ取得

    Returns:
        ジャンル設定のdictリスト（sub_themesはlistに変換済み）
    """
    with get_connection() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM genre_settings WHERE active = 1 ORDER BY weight DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM genre_settings ORDER BY weight DESC"
            ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["sub_themes"] = json.loads(d["sub_themes"])
        result.append(d)
    return result


def update_genre_setting(
    genre_id: int,
    weight: Optional[int] = None,
    active: Optional[bool] = None,
    sub_themes: Optional[list] = None,
) -> None:
    """
    ジャンル設定を更新する。

    Args:
        genre_id: 更新対象のジャンルID
        weight: 重み（1〜10）
        active: 有効/無効
        sub_themes: サブテーマリスト
    """
    updates = {}
    if weight is not None:
        updates["weight"] = weight
    if active is not None:
        updates["active"] = 1 if active else 0
    if sub_themes is not None:
        updates["sub_themes"] = json.dumps(sub_themes, ensure_ascii=False)

    if not updates:
        return

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [genre_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE genre_settings SET {set_clause} WHERE id = ?", values
        )
