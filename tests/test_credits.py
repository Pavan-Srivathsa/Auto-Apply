"""The header's spend counter: the ledger is read locally, and the balance is
read from OpenRouter at most every few minutes."""
import json
from datetime import date

from fastapi.testclient import TestClient

from server import system
from server.app import app


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._body


def test_counter_reads_ledger_and_caches_the_balance(tmp_path, monkeypatch):
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({date.today().isoformat(): {"openrouter:z-ai/glm-5.3": {"calls": 3, "in": 1, "out": 1, "usd": 0.5},
                                                            "jev": {"calls": 2, "in": 1, "out": 0, "usd": 0.25}}}))
    monkeypatch.setenv("AUTOPILOT_USAGE", str(usage))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return Reply(200, {"data": {"total_credits": 20, "total_usage": 6.5}})

    import httpx
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setitem(system._balance, "at", 0.0)
    client = TestClient(app)
    out = client.get("/system/credits").json()
    assert out["today"] == 0.75 and out["week"] == 0.75
    assert out["openrouter"] == {"ok": True, "left": 13.5, "used": 6.5, "total": 20.0}
    client.get("/system/credits")
    assert len(calls) == 1                     # cached: no second read
    client.get("/system/credits?fresh=1")
    assert len(calls) == 2


def test_an_expired_key_is_said_not_hidden(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-old")
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply(401, {"error": {"message": "API key expired."}}))
    assert system._read_balance() == {"ok": False, "error": "key expired"}
