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


def test_message_scan_passes_days_back(monkeypatch):
    captured = {}

    def fake_scan(days_back=None):
        captured["days_back"] = days_back
        return {"stored": 7, "full_rescan": True}

    monkeypatch.setattr("core.messages.pipeline.run_full_scan", fake_scan)
    client = TestClient(server.app)
    resp = client.post("/api/messages/scan?days_back=30")
    assert resp.status_code == 200
    assert resp.json()["stored"] == 7
    assert captured["days_back"] == 30


def test_message_status_endpoint(monkeypatch):
    monkeypatch.setattr("core.messages.vector_store.count", lambda: 42)
    monkeypatch.setattr("core.messages.chat_db.has_access", lambda: True)
    client = TestClient(server.app)
    resp = client.get("/api/messages/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_messages"] == 42
    assert body["full_disk_access"] is True
    assert "runtime_executable" in body


def test_open_settings_invokes_open(monkeypatch):
    calls = {}

    def fake_run(args, **k):
        calls["args"] = args
        return None

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("sys.platform", "darwin")
    client = TestClient(server.app)
    resp = client.post("/api/messages/open-settings")
    assert resp.status_code == 200
    assert resp.json()["opened"] is True
    assert calls["args"][0] == "open"
    assert "Privacy_AllFiles" in calls["args"][1]
