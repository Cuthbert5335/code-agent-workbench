from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json() == {
        "status": "ok",
        "service": "CodeXXX API",
        "version": "0.1.0",
    }


def test_openapi_is_available() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/health" in response.json()["paths"]
