from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["data"] == {"status": "ok", "service": "crowdshield-backend"}
    assert body["error"] is None
    assert isinstance(body["meta"]["request_id"], str)
    assert len(body["meta"]["request_id"]) > 0
