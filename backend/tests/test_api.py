"""Интеграционные тесты API: сессия каталога + оркестратор на фейках.

Подменяем app.state.orchestrator до старта приложения — так тестируем
HTTP-контракт без сети, Docker и Ollama.
"""

import pytest
from fastapi.testclient import TestClient

from app.chat.orchestrator import REFUSAL_TEMPLATE, Orchestrator
from app.core.config import Settings
from app.main import app
from tests.fakes import FakeLlm
from tests.test_retrieval import _store_with_chunks


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        settings = Settings(app_env="test")
        orch = Orchestrator(settings, _store_with_chunks(), FakeLlm())
        c.app.state.orchestrator = orch
        yield c


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_services_list(client: TestClient) -> None:
    services = client.get("/api/services").json()
    assert len(services) >= 3
    assert {"id", "title", "checklist", "sources"} <= set(services[0].keys())


def test_service_by_id_and_404(client: TestClient) -> None:
    service = client.get("/api/services/posobie-rozhdenie").json()
    assert "рождени" in service["title"].lower()
    assert client.get("/api/services/net-takoy").status_code == 404


def test_situations(client: TestClient) -> None:
    situations = client.get("/api/situations").json()
    assert situations, "Ситуации обязаны собираться из каталога услуг"
    first = situations[0]
    assert {"id", "title", "service_ids"} <= set(first.keys())

    detail = client.get(f"/api/situations/{first['id']}").json()
    assert detail["services"], "Деталь ситуации содержит услуги"
    assert client.get("/api/situations/net-takoy").status_code == 404


def test_chat_endpoint_contract(client: TestClient) -> None:
    """API /api/chat отдаёт {answer, citations[], refusal, latency_ms}."""
    resp = client.post(
        "/api/chat", json={"message": "какой размер пособия на ребёнка?"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {"answer", "citations", "refusal", "latency_ms"} <= set(body.keys())


def test_chat_refusal_contract(client: TestClient) -> None:
    resp = client.post("/api/chat", json={"message": "как выиграть в лотерею?"})
    body = resp.json()
    assert body["refusal"] is True
    assert body["answer"] == REFUSAL_TEMPLATE


def test_chat_validation(client: TestClient) -> None:
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
