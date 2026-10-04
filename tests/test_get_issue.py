from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

KNOWN_ISSUE_ID = "64f1c0a2b3d4e5f607182930"
KNOWN_ISSUE_TITLE = "Example issue"


def test_get_issue_returns_id_and_title():
    response = client.get(f"/issues/{KNOWN_ISSUE_ID}")

    assert response.status_code == 200
    assert response.json() == {
        "id": KNOWN_ISSUE_ID,
        "title": KNOWN_ISSUE_TITLE,
    }


def test_get_unknown_issue_returns_404():
    response = client.get("/issues/does-not-exist")

    assert response.status_code == 404
