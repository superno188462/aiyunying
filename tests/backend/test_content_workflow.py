"""Verify the reviewed API flow without external XHS or gateway calls."""
from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api import content_workflows as workflow_api
from backend.app.core.database import Base, get_db
from backend.app.core.deps import get_current_user
from backend.app.main import app
from backend.app.models import User


def test_full_content_workflow_with_manual_gates(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    user = User(username="editor", password_hash="unused")
    db.add(user)
    db.commit()
    db.refresh(user)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(workflow_api, "_get_owned_pc_account_cookies", lambda *_: "cookies")
    monkeypatch.setattr(workflow_api, "CARD_ROOT", tmp_path)

    class FakeXhs:
        def __init__(self, _cookies):
            pass

        def search_note(self, _topic, **_kwargs):
            return True, "ok", {"data": {"items": [{"note_card": {"note_id": "n1", "title": "热门笔记", "desc": "参考内容", "interact_info": {"liked_count": "100"}}}]}}

    monkeypatch.setattr(workflow_api, "XhsPcApiAdapter", FakeXhs)

    def fake_graph(action, _item, **_kwargs):
        if action == "topics":
            return {"candidates": [{"title": f"题目 {index}", "audience": "新手", "angle": "方法", "difference": "更具体", "evidence": "热门笔记"} for index in range(3)]}
        if action == "draft":
            return {"draft_title": "长文标题", "draft_body": "正文" * 100}
        return {"cards": [{"headline": f"卡片 {index}", "body": "精简内容"} for index in range(3)], "xhs_title": "小红书标题", "xhs_caption": "小红书配文"}

    monkeypatch.setattr(workflow_api, "_graph", fake_graph)

    def fake_render(workflow_id, index, _headline, _body, root):
        directory = root / str(workflow_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"card-{index + 1}.png"
        Image.new("RGB", (2, 2)).save(path)
        return path

    monkeypatch.setattr(workflow_api, "render_card", fake_render)
    monkeypatch.setattr(workflow_api.SeedreamImageClient, "generate", lambda *_: Image.new("RGB", (2, 2)))
    client = TestClient(app)
    try:
        created = client.post("/api/content-workflows", json={"topic": "知识管理", "account_id": 1}).json()
        workflow_id = created["id"]
        assert created["stage"] == "created"
        assert client.post(f"/api/content-workflows/{workflow_id}/topics", json={"version": 1}).status_code == 409

        current = client.post(f"/api/content-workflows/{workflow_id}/research", json={"version": 1}).json()
        assert current["references"][0]["likes"] == 100
        assert client.post(f"/api/content-workflows/{workflow_id}/research", json={"version": 1}).json()["detail"]["code"] == "STALE_VERSION"

        current = client.post(f"/api/content-workflows/{workflow_id}/topics", json={"version": current["version"]}).json()
        assert len(current["candidates"]) == 3
        current = client.post(f"/api/content-workflows/{workflow_id}/select-topic", json={"version": current["version"], "index": 1}).json()
        current = client.post(f"/api/content-workflows/{workflow_id}/draft/generate", json={"version": current["version"], "feedback": ""}).json()
        assert current["stage"] == "draft_ready"
        current = client.put(f"/api/content-workflows/{workflow_id}/draft", json={"version": current["version"], "title": "人工标题", "body": "人工正文"}).json()
        assert current["draft_versions"][-1]["source"] == "human"
        current = client.post(f"/api/content-workflows/{workflow_id}/draft/approve", json={"version": current["version"]}).json()
        current = client.post(f"/api/content-workflows/{workflow_id}/cards/generate", json={"version": current["version"]}).json()
        assert current["stage"] == "cards_ready"
        def unavailable_gateway(*_args, **_kwargs):
            raise AssertionError("图片重绘不应调用文案网关")

        monkeypatch.setattr(workflow_api, "_graph", unavailable_gateway)
        current = client.post(f"/api/content-workflows/{workflow_id}/cards/generate", json={"version": current["version"]}).json()
        assert current["stage"] == "cards_ready"
        current = client.post(f"/api/content-workflows/{workflow_id}/cards/0/visual/regenerate", json={"version": current["version"], "visual_prompt": "纸艺森林中的知识树"}).json()
        assert current["cards"][0]["visual_prompt"] == "纸艺森林中的知识树"
        preview = client.get(f"/api/content-workflows/{workflow_id}/cards/0/image?v={current['version']}")
        assert preview.status_code == 200
        assert preview.headers["cache-control"] == "no-store"
        assert client.get(f"/api/content-workflows/{workflow_id}/export").status_code == 200
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
