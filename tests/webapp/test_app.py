# tests/webapp/test_app.py
from fastapi.testclient import TestClient
from webapp.app import create_app


def test_health_endpoint():
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
