from fastapi.testclient import TestClient

import app.server as server


def test_message_sync_endpoint(monkeypatch):
    monkeypatch.setattr("core.messages.sync.incremental_sync",
                        lambda **k: {"new_messages": 3, "full_rescan": False, "stored": 3})
    client = TestClient(server.app)
    resp = client.post("/api/messages/sync")
    assert resp.status_code == 200
    assert resp.json()["new_messages"] == 3


def test_message_sync_reports_access_error(monkeypatch):
    from core.messages.chat_db import MessagesAccessError

    def boom(**k):
        raise MessagesAccessError("Grant Full Disk Access")

    monkeypatch.setattr("core.messages.sync.incremental_sync", boom)
    client = TestClient(server.app)
    resp = client.post("/api/messages/sync")
    assert resp.status_code == 400
    assert "Full Disk Access" in resp.json()["detail"]
