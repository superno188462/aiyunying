"""主题研究、人工审核和双平台素材导出的 HTTP 契约。"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.adapters.xhs.pc_api_adapter import XhsPcApiAdapter
from backend.app.api.platforms.xhs.pc import _get_owned_pc_account_cookies, _normalize_search_item
from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.models import ContentWorkflow, User
from backend.app.services.content_cards import render_card, save_background
from backend.app.services.content_graph import GatewayTextClient, build_generation_graph
from backend.app.services.seedream_images import SeedreamConfigurationError, SeedreamGenerationError, SeedreamImageClient

router = APIRouter(prefix="/content-workflows", tags=["content-workflows"])
CARD_ROOT = Path(os.getenv("CONTENT_CARD_DIR", "backend/app/storage/content_cards"))


class CreateWorkflow(BaseModel):
    """创建一次内容生产任务。"""

    topic: str = Field(min_length=2, max_length=120, description="用户输入的主题")
    account_id: int = Field(gt=0, description="已绑定的小红书 PC 账号 ID")


class ManualReference(BaseModel):
    """人工录入的一条笔记，只接收用于选题的公开文本。"""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=3000)
    note_url: str = Field(default="", max_length=2048)
    likes: int | None = Field(default=None, ge=0)
    collects: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)


class CreateManualWorkflow(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    references: list[ManualReference] = Field(default_factory=list, max_length=5)


class VersionRequest(BaseModel):
    """乐观并发版本；过期请求返回 409。"""

    version: int = Field(gt=0, description="详情响应中的版本号")


class SelectTopicRequest(VersionRequest):
    index: int = Field(ge=0, le=4, description="所选候选题目的零基序号")


class GenerateDraftRequest(VersionRequest):
    feedback: str = Field(default="", max_length=1000, description="人工修改意见，可为空")


class SaveDraftRequest(VersionRequest):
    title: str = Field(min_length=1, max_length=300, description="人工修订的公众号标题")
    body: str = Field(min_length=1, max_length=15000, description="人工修订的 Markdown 正文")


class UpdateCardRequest(VersionRequest):
    headline: str = Field(min_length=1, max_length=50, description="卡片标题")
    body: str = Field(min_length=1, max_length=300, description="卡片摘要")


class RegenerateCardVisualRequest(VersionRequest):
    visual_prompt: str = Field(min_length=5, max_length=200, description="单张卡片的无字插画场景")


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _owned(db: Session, user: User, workflow_id: int) -> ContentWorkflow:
    item = db.get(ContentWorkflow, workflow_id)
    if item is None or item.user_id != user.id:
        raise _error(404, "NOT_FOUND", "任务不存在")
    return item


def _check(item: ContentWorkflow, version: int, stages: tuple[str, ...]) -> None:
    if item.version != version:
        raise _error(409, "STALE_VERSION", "任务已更新，请刷新后重试")
    if item.stage not in stages:
        raise _error(409, "INVALID_STAGE", "当前阶段不能执行此操作")


def _serialize(item: ContentWorkflow) -> dict[str, Any]:
    return {
        "id": item.id,
        "topic": item.topic,
        "account_id": item.account_id,
        "source_mode": "manual" if item.account_id is None else "account",
        "stage": item.stage,
        "version": item.version,
        "references": item.references or [],
        "candidates": item.candidates or [],
        "selected_topic": item.selected_topic or {},
        "draft_title": item.draft_title,
        "draft_body": item.draft_body,
        "draft_versions": item.draft_versions or [],
        "cards": item.cards or [],
        "xhs_title": item.xhs_title,
        "xhs_caption": item.xhs_caption,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _finish(db: Session, item: ContentWorkflow, stage: str) -> dict[str, Any]:
    item.stage = stage
    item.version += 1
    db.commit()
    db.refresh(item)
    return _serialize(item)


def _card_root(card: dict[str, Any]) -> Path:
    batch = card.get("asset_batch")
    if not batch:
        return CARD_ROOT  # 兼容已有的固定模板图片。
    if not isinstance(batch, str) or not re.fullmatch(r"[0-9a-f]{32}", batch):
        raise _error(500, "INVALID_ASSET_BATCH", "图片资源标识异常")
    return CARD_ROOT / ".batches" / batch


def _card_path(item: ContentWorkflow, index: int) -> Path:
    return _card_root(item.cards[index]) / str(item.id) / f"card-{index + 1}.png"


def _cleanup_batch(batch_root: Path, workflow_id: int) -> None:
    """失败时只删除本次新建的 PNG，不触碰已有任务资源。"""
    task_dir = batch_root / str(workflow_id)
    if task_dir.is_dir():
        for path in task_dir.glob("*.png"):
            path.unlink(missing_ok=True)
        task_dir.rmdir()
    if batch_root.is_dir():
        batch_root.rmdir()


def _generate_visual(workflow_id: int, index: int, card: dict[str, str], batch_root: Path, client: SeedreamImageClient) -> None:
    visual_prompt = card.get("visual_prompt") or card["headline"]
    artwork = client.generate(visual_prompt, index)
    save_background(workflow_id, index, artwork, batch_root)
    render_card(workflow_id, index, card["headline"], card["body"], batch_root)


def _graph(action: str, item: ContentWorkflow, **extra: Any) -> dict[str, Any]:
    try:
        return build_generation_graph(GatewayTextClient()).invoke(
            {
                "action": action,
                "topic": item.topic,
                "references": item.references or [],
                "selected_topic": item.selected_topic or {},
                "draft_title": item.draft_title,
                "draft_body": item.draft_body,
                **extra,
            }
        )
    except RuntimeError as exc:
        raise _error(503, "GATEWAY_CONFIG", str(exc)) from exc
    except ValueError as exc:
        raise _error(502, "GENERATION_INVALID", str(exc)) from exc
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        if status_code in (401, 403):
            message = "AI 网关认证失败，请检查 API Key 和模型权限"
        elif status_code == 422:
            message = "AI 网关拒绝请求参数（HTTP 422），请检查模型配置"
        elif status_code == 429:
            message = "AI 网关请求过于频繁或额度不足，请稍后重试"
        else:
            message = f"AI 网关返回 HTTP {status_code}，请检查网关服务"
        raise _error(502, "GATEWAY_HTTP_ERROR", message) from exc
    except requests.Timeout as exc:
        raise _error(504, "GATEWAY_TIMEOUT", "AI 网关响应超时，请稍后重试") from exc
    except requests.ConnectionError as exc:
        raise _error(502, "GATEWAY_CONNECTION_ERROR", "无法连接 AI 网关，请检查网络或网关地址") from exc
    except Exception as exc:
        raise _error(502, "GATEWAY_FAILED", "AI 网关暂时不可用，请稍后重试") from exc


@router.post("", summary="创建内容策划任务", description="要求当前用户拥有指定的小红书 PC 账号。")
def create_workflow(payload: CreateWorkflow, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    _get_owned_pc_account_cookies(db, user, payload.account_id)
    item = ContentWorkflow(user_id=user.id, account_id=payload.account_id, topic=payload.topic.strip(), stage="created", version=1)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize(item)


@router.post("/manual", summary="仅凭主题或手动参考笔记创建免绑定内容策划任务")
def create_manual_workflow(payload: CreateManualWorkflow, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    topic = payload.topic.strip()
    if len(topic) < 2:
        raise _error(422, "INVALID_TOPIC", "主题至少需要 2 个字")
    references = []
    for index, note in enumerate(payload.references):
        title, content, note_url = note.title.strip(), note.content.strip(), note.note_url.strip()
        if not title or not content:
            raise _error(422, "INVALID_REFERENCE", f"第 {index + 1} 条参考需要标题和内容")
        if note_url:
            try:
                parsed = urlsplit(note_url)
                valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not any(char.isspace() for char in note_url)
            except ValueError:
                valid_url = False
            if not valid_url:
                raise _error(422, "INVALID_REFERENCE_URL", f"第 {index + 1} 条参考链接需为 HTTP 或 HTTPS 地址")
        references.append({
            "note_id": f"manual-{index + 1}",
            "note_url": note_url,
            "title": title,
            "content": content,
            "likes": note.likes,
            "collects": note.collects,
            "comments": note.comments,
            "timestamp": 0,
        })
    item = ContentWorkflow(user_id=user.id, account_id=None, topic=topic, stage="researched", version=1, references=references)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize(item)


@router.get("", summary="列出我的内容策划任务")
def list_workflows(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    items = db.scalars(select(ContentWorkflow).where(ContentWorkflow.user_id == user.id).order_by(ContentWorkflow.updated_at.desc()).limit(50)).all()
    return [_serialize(item) for item in items]


@router.get("/{workflow_id}", summary="读取任务及所有审核版本")
def get_workflow(workflow_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _serialize(_owned(db, user, workflow_id))


@router.post("/{workflow_id}/research", summary="采集并排序小红书热门参考")
def research(workflow_id: int, payload: VersionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("created", "researched"))
    cookies = _get_owned_pc_account_cookies(db, user, item.account_id)
    try:
        success, _, raw = XhsPcApiAdapter(cookies).search_note(item.topic, page=1, sort_type_choice=1)
    except Exception as exc:
        raise _error(502, "XHS_SEARCH_FAILED", "小红书搜索失败，请检查账号后重试") from exc
    if not success:
        raise _error(502, "XHS_SEARCH_FAILED", "小红书搜索失败，请检查账号后重试")
    rows = ((raw or {}).get("data") or {}).get("items") or []
    refs = [_normalize_search_item(row) for row in rows if isinstance(row, dict) and row.get("model_type") not in ("rec_query", "hot_query")]
    refs.sort(key=lambda row: row["likes"] + 2 * row["collects"] + row["comments"], reverse=True)
    item.references = [{key: row.get(key) for key in ("note_id", "note_url", "title", "content", "likes", "collects", "comments", "timestamp")} for row in refs[:15]]
    if not item.references:
        raise _error(422, "NO_REFERENCES", "没有找到相关笔记，请换一个主题")
    return _finish(db, item, "researched")


@router.post("/{workflow_id}/topics", summary="用 LangGraph 生成 3 至 5 个选题")
def generate_topics(workflow_id: int, payload: VersionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("researched", "topics_ready"))
    result = _graph("topics", item)
    db.refresh(item)
    _check(item, payload.version, ("researched", "topics_ready"))
    item.candidates = result["candidates"]
    return _finish(db, item, "topics_ready")


@router.post("/{workflow_id}/select-topic", summary="人工确认一个子选题")
def select_topic(workflow_id: int, payload: SelectTopicRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("topics_ready",))
    if payload.index >= len(item.candidates or []):
        raise _error(422, "INVALID_INDEX", "选题序号超出范围")
    item.selected_topic = item.candidates[payload.index]
    return _finish(db, item, "topic_selected")


@router.post("/{workflow_id}/draft/generate", summary="生成或按意见重写公众号文案")
def generate_draft(workflow_id: int, payload: GenerateDraftRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("topic_selected", "draft_ready"))
    result = _graph("draft", item, previous_draft=item.draft_body, feedback=payload.feedback)
    db.refresh(item)
    _check(item, payload.version, ("topic_selected", "draft_ready"))
    item.draft_title, item.draft_body = result["draft_title"], result["draft_body"]
    item.draft_versions = [*(item.draft_versions or []), {"title": item.draft_title, "body": item.draft_body, "source": "ai"}]
    return _finish(db, item, "draft_ready")


@router.put("/{workflow_id}/draft", summary="保存人工修改的文案")
def save_draft(workflow_id: int, payload: SaveDraftRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("draft_ready",))
    item.draft_title, item.draft_body = payload.title.strip(), payload.body.strip()
    item.draft_versions = [*(item.draft_versions or []), {"title": item.draft_title, "body": item.draft_body, "source": "human"}]
    return _finish(db, item, "draft_ready")


@router.post("/{workflow_id}/draft/approve", summary="人工确认文案后解锁图片生成")
def approve_draft(workflow_id: int, payload: VersionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("draft_ready",))
    if not item.draft_body.strip():
        raise _error(422, "EMPTY_DRAFT", "文案不能为空")
    return _finish(db, item, "draft_approved")


@router.post("/{workflow_id}/cards/generate", summary="首次提炼长文并生图；重绘复用卡片内容直接调用火山方舟")
def generate_cards(workflow_id: int, payload: VersionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("draft_approved", "cards_ready"))
    if item.stage == "cards_ready" and item.cards:
        # 重绘已有图片无需重新提炼文案，避免生图受文案网关影响。
        result = {"cards": item.cards, "xhs_title": item.xhs_title, "xhs_caption": item.xhs_caption}
    else:
        try:
            result = _graph("cards", item)
        except HTTPException as exc:
            if isinstance(exc.detail, dict):
                exc.detail = {**exc.detail, "message": f"图片摘要与提示词提炼失败（文案网关）：{exc.detail['message']}"}
            raise
    batch = uuid4().hex
    batch_root = CARD_ROOT / ".batches" / batch
    try:
        client = SeedreamImageClient()
        for index, card in enumerate(result["cards"]):
            _generate_visual(item.id, index, card, batch_root, client)
    except SeedreamConfigurationError as exc:
        _cleanup_batch(batch_root, item.id)
        raise _error(503, "IMAGE_CONFIG", str(exc)) from exc
    except SeedreamGenerationError as exc:
        _cleanup_batch(batch_root, item.id)
        raise _error(502, "IMAGE_GENERATION_FAILED", str(exc)) from exc
    except Exception as exc:
        _cleanup_batch(batch_root, item.id)
        raise _error(503, "CARD_RENDER_FAILED", "图片渲染失败，请检查中文字体") from exc
    db.refresh(item)
    try:
        _check(item, payload.version, ("draft_approved", "cards_ready"))
    except HTTPException:
        _cleanup_batch(batch_root, item.id)
        raise
    item.cards = [{**card, "asset_batch": batch} for card in result["cards"]]
    item.xhs_title, item.xhs_caption = result["xhs_title"], result["xhs_caption"]
    return _finish(db, item, "cards_ready")


@router.put("/{workflow_id}/cards/{index}", summary="人工编辑卡片并重新渲染")
def update_card(workflow_id: int, index: int, payload: UpdateCardRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("cards_ready",))
    if index < 0 or index >= len(item.cards or []):
        raise _error(404, "CARD_NOT_FOUND", "图片不存在")
    try:
        render_card(item.id, index, payload.headline.strip(), payload.body.strip(), _card_root(item.cards[index]))
    except Exception as exc:
        raise _error(503, "CARD_RENDER_FAILED", "图片渲染失败，请检查中文字体") from exc
    cards = list(item.cards)
    cards[index] = {**cards[index], "headline": payload.headline.strip(), "body": payload.body.strip()}
    item.cards = cards
    return _finish(db, item, "cards_ready")


@router.post("/{workflow_id}/cards/{index}/visual/regenerate", summary="调整提示词并用 Seedream 重绘单张背景")
def regenerate_card_visual(workflow_id: int, index: int, payload: RegenerateCardVisualRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = _owned(db, user, workflow_id)
    _check(item, payload.version, ("cards_ready",))
    if index < 0 or index >= len(item.cards or []):
        raise _error(404, "CARD_NOT_FOUND", "图片不存在")
    batch = uuid4().hex
    batch_root = CARD_ROOT / ".batches" / batch
    card = {**item.cards[index], "visual_prompt": payload.visual_prompt.strip()}
    try:
        _generate_visual(item.id, index, card, batch_root, SeedreamImageClient())
    except SeedreamConfigurationError as exc:
        _cleanup_batch(batch_root, item.id)
        raise _error(503, "IMAGE_CONFIG", str(exc)) from exc
    except SeedreamGenerationError as exc:
        _cleanup_batch(batch_root, item.id)
        raise _error(502, "IMAGE_GENERATION_FAILED", str(exc)) from exc
    except Exception as exc:
        _cleanup_batch(batch_root, item.id)
        raise _error(503, "CARD_RENDER_FAILED", "图片渲染失败，请检查中文字体") from exc
    db.refresh(item)
    try:
        _check(item, payload.version, ("cards_ready",))
    except HTTPException:
        _cleanup_batch(batch_root, item.id)
        raise
    cards = list(item.cards)
    cards[index] = {**card, "asset_batch": batch}
    item.cards = cards
    return _finish(db, item, "cards_ready")


@router.get("/{workflow_id}/cards/{index}/image", summary="下载单张 PNG 卡片")
def card_image(workflow_id: int, index: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _owned(db, user, workflow_id)
    if item.stage != "cards_ready" or index < 0 or index >= len(item.cards or []):
        raise _error(404, "CARD_NOT_FOUND", "图片不存在")
    path = _card_path(item, index)
    if not path.is_file():
        raise _error(404, "CARD_FILE_MISSING", "图片文件已丢失，请重新生成")
    return FileResponse(path, media_type="image/png", filename=path.name, headers={"Cache-Control": "no-store"})


@router.get("/{workflow_id}/export", summary="导出公众号 Markdown 与小红书图文包")
def export_workflow(workflow_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _owned(db, user, workflow_id)
    if item.stage != "cards_ready":
        raise _error(409, "INVALID_STAGE", "请先确认文案并生成图片")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("wechat.md", f"# {item.draft_title}\n\n{item.draft_body}\n")
        archive.writestr("xiaohongshu.txt", f"{item.xhs_title}\n\n{item.xhs_caption}\n")
        archive.writestr("sources.json", json.dumps(item.references or [], ensure_ascii=False, indent=2))
        for index in range(len(item.cards or [])):
            path = _card_path(item, index)
            if not path.is_file():
                raise _error(409, "CARD_FILE_MISSING", "图片文件已丢失，请重新生成")
            archive.write(path, f"cards/{path.name}")
    stream.seek(0)
    return StreamingResponse(stream, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="content-{item.id}.zip"'})
