from fastapi.testclient import TestClient
from app import APP, parse_episode_id

client=TestClient(APP)


def test_link_parsing():
    assert parse_episode_id("https://open.spotify.com/episode/0ZLhKjfvOYlosTelLqBaqZ?si=abc") == "0ZLhKjfvOYlosTelLqBaqZ"


def test_invalid_url_protection():
    for url in ["http://open.spotify.com/episode/0ZLhKjfvOYlosTelLqBaqZ", "https://wrong.example/episode/0ZLhKjfvOYlosTelLqBaqZ", "https://open.spotify.com/playlist/0ZLhKjfvOYlosTelLqBaqZ"]:
        assert client.get("/api/episode", params={"url": url}).status_code == 422


def test_local_frontend_works():
    response = client.get("/")
    assert response.status_code == 200
    assert "Podcast Clip Studio" in response.text
    assert "Spotify episode" in response.text


def test_unknown_job():
    assert client.get("/api/jobs/unknownid").status_code == 404


def test_hosted_requires_password(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEPLOY", "1")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert client.get("/").status_code == 503
    assert client.get("/health").status_code == 200


def test_hosted_password_auth(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEPLOY", "1")
    monkeypatch.setenv("APP_PASSWORD", "secret-test-password")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("pravesh", "wrong")).status_code == 401
    assert client.get("/", auth=("pravesh", "secret-test-password")).status_code == 200
    assert client.get("/api/jobs").status_code == 401
