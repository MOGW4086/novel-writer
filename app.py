"""
FastAPI Webアプリ。
小説の閲覧・フィードバック・読書進捗管理を提供する。

起動方法:
    uvicorn app:app --reload --port 8000
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import db
import knowledge
import notifier

app = FastAPI(title="novel-reader")
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)


def get_db():
    """FastAPI依存関数: dbモジュールを返す。テストでdependency_overrideにより差し替え可能。"""
    return db


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
    """一覧表示用に is_new / reading_status / has_feedback を付与したコピーを返す。"""
    return {
        **novel,
        "is_new": _is_new(novel.get("generated_at", "")),
        "reading_status": _reading_status(novel),
        "has_feedback": bool(novel.get("feedback_count", 0)),
    }


# ──────────────────────────────────────────────
# トップページ（小説一覧）
# ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, _db=Depends(get_db)):
    """
    小説一覧ページ。シリーズ一覧とスタンドアロン小説を表示する。
    スタンドアロン小説は「フィードバック待ち」と「未読・読書中」に分けて渡す。
    """
    series_list = _db.get_series_list()
    all_standalone = [_enrich_novel(n) for n in _db.get_standalone_novels(limit=100)]

    # シリーズにも新着フラグを付与（最終更新日で判定）
    for s in series_list:
        s["is_new"] = _is_new(s.get("latest_generated_at") or "")

    # 読了済みでフィードバック未記入の作品を分離
    feedback_needed = [n for n in all_standalone if n["reading_status"] == "completed" and not n["has_feedback"]]
    feedback_needed_ids = {n["id"] for n in feedback_needed}
    standalone = [n for n in all_standalone if n["id"] not in feedback_needed_ids]

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "series_list": series_list,
            "feedback_needed": feedback_needed,
            "standalone": standalone,
        },
    )


# ──────────────────────────────────────────────
# シリーズ詳細ページ
# ──────────────────────────────────────────────

@app.get("/series/{series_id}", response_class=HTMLResponse)
async def series_detail(request: Request, series_id: int, _db=Depends(get_db)):
    """
    シリーズ詳細ページ。エピソード一覧と読書進捗を表示する。
    """
    series = _db.get_series(series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="シリーズが見つかりません")

    novels = [_enrich_novel(n) for n in _db.get_novels_by_series(series_id)]

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
async def novel_detail(request: Request, novel_id: int, _db=Depends(get_db)):
    """
    小説閲覧ページ。本文・フィードバックフォーム・読書進捗を表示する。
    初回アクセス時は読書進捗レコードを作成する（scroll_percent=0）。
    """
    novel = _db.get_novel(novel_id)
    if novel is None:
        raise HTTPException(status_code=404, detail="小説が見つかりません")

    # 初回アクセス時は読書進捗を作成（opened_at を記録）
    progress = _db.get_reading_progress(novel_id)
    if progress is None:
        _db.upsert_reading_progress(novel_id, scroll_percent=0)
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
        series = _db.get_series(novel["series_id"])
        series_novels = [_enrich_novel(n) for n in _db.get_novels_by_series(novel["series_id"])]

    # 既存フィードバック
    feedbacks = _db.get_feedback(novel_id)

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
async def update_progress(novel_id: int, body: ProgressRequest, _db=Depends(get_db)):
    """
    スクロール進捗をJSONで受け取り更新する。
    scroll_percent が 95 以上の場合は読了とみなす。

    リクエストボディ: {"scroll_percent": 0〜100}
    """
    if _db.get_novel(novel_id) is None:
        raise HTTPException(status_code=404, detail="小説が見つかりません")

    is_completed = body.scroll_percent >= 95
    _db.upsert_reading_progress(novel_id, body.scroll_percent, is_completed)
    return JSONResponse({"ok": True, "scroll_percent": body.scroll_percent, "is_completed": is_completed})


# ──────────────────────────────────────────────
# フィードバック送信
# ──────────────────────────────────────────────

# 連続失敗がこの回数に達した場合にLINE通知を送る
_EXTRACTION_FAILURE_ALERT_THRESHOLD = 3


def _run_knowledge_extraction(comment: str, novel_id: int, _db) -> None:
    """バックグラウンドタスク: 知見抽出を実行し、結果をDBに記録する。失敗が続いたらLINE通知。"""
    try:
        knowledge.extract_and_save_knowledge(comment, novel_id=novel_id)
        _db.save_extraction_log(novel_id, "success")
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
        logger.warning("知見抽出に失敗しました（novel_id=%d）: %s", novel_id, exc, exc_info=True)
        _db.save_extraction_log(novel_id, "failure", error_type=error_type, error_message=error_message)

        consecutive = _db.get_consecutive_failure_count()
        if consecutive == _EXTRACTION_FAILURE_ALERT_THRESHOLD:
            notifier.send_extraction_error_notification(consecutive, error_type, error_message)


@app.post("/novels/{novel_id}/feedback")
async def submit_feedback(
    novel_id: int,
    background_tasks: BackgroundTasks,
    rating: int = Form(...),
    comment: str = Form("", max_length=2000),
    _db=Depends(get_db),
):
    """
    フィードバック（評価・コメント）を保存してリダイレクトする。
    コメントがある場合は知見抽出をバックグラウンドで実行する。
    """
    if _db.get_novel(novel_id) is None:
        raise HTTPException(status_code=404, detail="小説が見つかりません")

    if not 1 <= rating <= 5:
        raise HTTPException(status_code=422, detail="評価は1〜5で指定してください")

    clean_comment = comment.strip()
    _db.save_feedback(novel_id, rating, clean_comment)

    if clean_comment:
        background_tasks.add_task(_run_knowledge_extraction, clean_comment, novel_id, _db)

    return RedirectResponse(url=f"/novels/{novel_id}#feedback", status_code=303)
