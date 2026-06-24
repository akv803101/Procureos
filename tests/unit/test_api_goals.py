"""POST /goals + GET /goals/{id} via the FastAPI TestClient."""
import api.routes.goals as goals_route
from api.main import app
from core.db import Goal
from fastapi.testclient import TestClient

client = TestClient(app)


def test_create_goal_returns_202(monkeypatch):
    async def fake_intent(raw, city, **k):
        return {"category": "fb", "budget_hint": 20000}

    async def fake_create(goal):
        return "goal-1"

    async def fake_process(goal_id, **k):
        return None

    monkeypatch.setattr(goals_route, "parse_intent", fake_intent)
    monkeypatch.setattr(goals_route._store, "create_goal", fake_create)
    monkeypatch.setattr(goals_route, "process_goal", fake_process)

    r = client.post("/goals", json={"raw_input": "snacks for 50 Koramangala", "company_id": "c1"})
    assert r.status_code == 202
    body = r.json()
    assert body["success"] is True
    assert body["data"]["goal_id"] == "goal-1"


def test_create_goal_intent_unclear_returns_422(monkeypatch):
    async def boom(raw, city, **k):
        raise RuntimeError("all models failed")

    monkeypatch.setattr(goals_route, "parse_intent", boom)
    r = client.post("/goals", json={"raw_input": "???", "company_id": "c1"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "intent_unclear"


def test_get_goal_status(monkeypatch):
    async def fake_get(goal_id):
        return Goal(id=goal_id, status="pending_approval", category="fb",
                    options=[{"vendor_id": "p1"}], collected_quotes=[{}, {}])

    monkeypatch.setattr(goals_route._store, "get_goal", fake_get)
    r = client.get("/goals/goal-1")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "pending_approval"
    assert data["collected_quotes"] == 2
