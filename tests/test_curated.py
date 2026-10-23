"""The curated watchlist: a board counts only when it is shown to be the
company's (named, and about AI), the import is idempotent and never watches
a board twice, and every curated watch holds instead of queueing."""
import json

import httpx
import pytest

from scout import curated, store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_CURATED", str(tmp_path / "registry.json"))
    monkeypatch.setattr(store, "PATH", tmp_path / "scout.json", raising=False)
    monkeypatch.setattr(store, "path", lambda: tmp_path / "scout.json", raising=False)


BOARDS = {
    "/posting-api/job-board/acmeai": {"jobs": [{"title": "AI Engineer", "descriptionPlain": "Acme AI builds agents for banks."}]},
    "/posting-api/job-board/acme": {"jobs": [{"title": "Nurse", "descriptionPlain": "Acme Clinic hires nurses."}]},
    "/v1/boards/galileo": {"name": "Galileo"},
    "/v1/boards/galileo/jobs": {"jobs": [{"title": "Dermatologist", "content": "Galileo is a clinic."}]},
}


def client():
    def handler(request):
        body = BOARDS.get(request.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_board_must_name_the_company_and_be_about_ai():
    found = curated.resolve("Acme AI", client(), careers_pages=False)
    assert found["status"] == "resolved" and found["slug"] == "acmeai" and "name the company" in found["verified_by"]
    monkey = curated.resolve("Galileo", client(), careers_pages=False)          # the board is named Galileo, but hires dermatologists
    assert monkey["status"] == "unresolved" and monkey["tried"]


def test_the_import_is_idempotent_links_a_manual_watch_and_holds(monkeypatch, tmp_path):
    from scout import Watch
    watches = []
    monkeypatch.setattr(store, "watches", lambda: list(watches))

    def add_watch(w):
        for x in watches:
            if x.id == w.id:
                return x, False
        watches.append(w)
        return w, True
    monkeypatch.setattr(store, "add_watch", add_watch)
    manual = Watch(url="https://jobs.ashbyhq.com/acme-two", company="Acme Two", provider="ashby", args={"slug": "acme-two"})
    watches.append(manual)
    rows = {"Acme AI": {"company": "Acme AI", "status": "resolved", "provider": "ashby", "slug": "acmeai",
                        "url": "https://jobs.ashbyhq.com/acmeai", "verified_by": "x", "open_roles": 1},
            "Acme Two": {"company": "Acme Two", "status": "resolved", "provider": "ashby", "slug": "acme-two",
                         "url": "https://jobs.ashbyhq.com/acme-two", "verified_by": "x", "open_roles": 1},
            "Nobody": {"company": "Nobody", "status": "unresolved", "tried": ["ashby:nobody"]}}
    monkeypatch.setattr(curated, "resolve", lambda name: rows[name])
    first = curated.import_all(list(rows), check=False)
    assert first["created"] == ["Acme AI"] and first["linked"] == ["Acme Two"] and first["unresolved"] == ["Nobody"]
    made = next(w for w in watches if w.company == "Acme AI")
    assert made.args["hold"] and made.args["curated"] == curated.TAG and made.args["years_min"] == curated.YEARS_MIN
    second = curated.import_all(list(rows), check=False)
    assert second["created"] == [] and len(watches) == 2                      # nothing twice
    registry = json.loads(curated.registry_path().read_text())
    assert registry["companies"]["Acme Two"]["manual"] is True
