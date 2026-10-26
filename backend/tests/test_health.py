from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_services_list() -> None:
    resp = client.get("/api/services")
    assert resp.status_code == 200
    services = resp.json()
    assert len(services) >= 3
    for service in services:
        assert {"id", "title", "checklist", "sources"} <= set(service.keys())


def test_service_by_id() -> None:
    resp = client.get("/api/services/posobie-rozhdenie")
    assert resp.status_code == 200
    assert "рождени" in resp.json()["title"].lower()


def test_service_404() -> None:
    assert client.get("/api/services/net-takoy").status_code == 404
