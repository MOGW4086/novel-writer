"""
FastAPI Webアプリ。
小説の閲覧・フィードバック・読書進捗管理を提供する。

起動方法:
    uvicorn app:app --reload --port 8000
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import db
import knowledge

app = FastAPI(title="novel-reader")
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)


class ProgressRequest(BaseModel):
    """読書進捗更新リクエストのスキーマ。"""
    scroll_percent: int = Field(..., ge=0, le=100)

# 新着判定の基準日数
NEW_DAYS = 7


def _is_new(generated_at: str) -> bool:
    """生成日が NEW_DAYS 以内なら True を返す。"""
    try:
        dt = datetime.fromisoformat(generated_at)
        threshold = datetime.now(timezone.utc) - timedelta(days=NEW_DAYS)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= threshold
    except ValueError:
        return False


def _reading_status(novel: dict) -> str:
    """読書状態を返す: 'unread' / 'reading' / 'completed'"""
    if novel.get("opened_at") is None:
        return "unread"
    if novel.get("is_completed"):
        return "completed"
    return "reading"


def _enrich_novel(novel: dict) -> dict:
    """一覧表示用に is_new と reading_status を付与したコピーを返す。"""
    return {
        **novel,
        "is_new": _is_new(novel.get("generated_at", "")),
        "reading_status": _reading_status(novel),
    }


# ──────────────────────────────────────────────
# トップページ（小説一覧）
# ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    小説一覧ページ。シリーズ一覧とスタンドアロン小説を表示する。
    """
    series_list = db.get_series_list()
    standalone = [_enrich_novel(n) for n in db.get_standalone_novels(limit=100)]

    # シリーズにも新着フラグを付与（最終更新日で判定）
    for s in series_list:
        s["is_new"] = _is_new(s.get("latest_generated_at") or "")

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "series_list": series_list,
            "standalone": standalone,
        },
    )


# ──────────────────────────────────────────────
# シリーズ詳細ページ
# ──────────────────────────────────────────────

@app.get("/series/{series_id}", response_class=HTMLResponse)
async def series_detail(request: Request, series_id: int):
    """
    シリーズ詳細ページ。エピソード一覧と読書進捗を表示する。
    """
    series = db.get_series(series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="シリーズが見つかりません")

    novels = [_enrich_novel(n) for n in db.get_novels_by_series(series_id)]

    return templates.TemplateResponse(
        request,
        "series.html",
        {
            "series": series,
            "novels": novels,
        },
    )


# ──────────────────────────────────────────────
# 小説閲覧ページ
# ──────────────────────────────────────────────

@app.get("/novels/{novel_id}", response_class=HTMLResponse)
async def novel_detail(request: Request, novel_id: int):
    """
    小説閲覧ページ。本文・フィードバックフォーム・読書進捗を表示する。
    初回アクセス時は読書進捗レコードを作成する（scroll_percent=0）。
    """
    novel = db.get_novel(novel_id)
    if novel is None:
        raise HTTPException(status_code=404, detail="小説が見つかりません")

    # 初回アクセス時は読書進捗を作成（opened_at を記録）
    progress = db.get_reading_progress(novel_id)
    if progress is None:
        db.upsert_reading_progress(novel_id, scroll_percent=0)
        # upsert 直後なのでデフォルト値でインメモリ構築し、DBへの再アクセスを省く
        now = datetime.now(timezone.utc).isoformat()
        progress = {
            "novel_id": novel_id,
            "scroll_percent": 0,
            "is_completed": 0,
            "opened_at": now,
            "last_read_at": now,
        }

    # 所属シリーズ情報（あれば取得）
    series = None
    series_novels: list[dict] = []
    if novel.get("series_id"):
        series = db.get_series(novel["series_id"])
        series_novels = [_enrich_novel(n) for n in db.get_novels_by_series(novel["series_id"])]

    # 既存フィードバック
    feedbacks = db.get_feedback(novel_id)

    return templates.TemplateResponse(
        request,
        "novel.html",
        {
            "novel": novel,
            "progress": progress,
            "series": series,
            "series_novels": series_novels,
            "feedbacks": feedbacks,
        },
    )


# ──────────────────────────────────────────────
# 読書進捗更新（Ajax）
# ──────────────────────────────────────────────

@app.post("/novels/{novel_id}/progress")
async def update_progress(novel_id: int, body: ProgressRequest):
    """
    スクロール進捗をJSONで受け取り更新する。
    scroll_percent が 95 以上の場合は読了とみなす。

    リクエストボディ: {"scroll_percent": 0〜100}
    """
    if db.get_novel(novel_id) is None:
        raise HTTPException(status_code=404, detail="小説が見つかりません")

    is_completed = body.scroll_percent >= 95
    db.upsert_reading_progress(novel_id, body.scroll_percent, is_completed)
    return JSONResponse({"ok": True, "scroll_percent": body.scroll_percent, "is_completed": is_completed})


# ──────────────────────────────────────────────
# フィードバック送信
# ──────────────────────────────────────────────

def _run_knowledge_extraction(comment: str, novel_id: int) -> None:
    """バックグラウンドタスク: 知見抽出を実行する。失敗してもログのみ記録する。"""
    try:
        knowledge.extract_and_save_knowledge(comment, novel_id=novel_id)
    except Exception as exc:
        logger.warning("知見抽出に失敗しました（novel_id=%d）: %s", novel_id, exc, exc_info=True)


@app.post("/novels/{novel_id}/feedback")
async def submit_feedback(
    novel_id: int,
    background_tasks: BackgroundTasks,
    rating: int = Form(...),
    comment: str = Form("", max_length=2000),
):
    """
    フィードバック（評価・コメント）を保存してリダイレクトする。
    コメントがある場合は知見抽出をバックグラウンドで実行する。
    """
    if db.get_novel(novel_id) is None:
        raise HTTPException(status_code=404, detail="小説が見つかりません")

    if not 1 <= rating <= 5:
        raise HTTPException(status_code=422, detail="評価は1〜5で指定してください")

    clean_comment = comment.strip()
    db.save_feedback(novel_id, rating, clean_comment)

    if clean_comment:
        background_tasks.add_task(_run_knowledge_extraction, clean_comment, novel_id)

    return RedirectResponse(url=f"/novels/{novel_id}#feedback", status_code=303)
