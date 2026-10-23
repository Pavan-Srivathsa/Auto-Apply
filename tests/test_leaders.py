"""Leadership research: the role's own engineering leaders first, a reason
and confidence on each, free search only (never an enrichment credit, never
a guessed email), and a LinkedIn note that can be drafted but never sent."""
import pytest

from outreach import apollo, leaders, store
from outreach.models import Contact, Draft
from tailor import outreach as writer


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE", tmp_path / "outreach.json")
    monkeypatch.setattr(store, "CACHE", tmp_path / "cache.json", raising=False)


def people():
    return [Contact.create("leadership", "Ann Chief", title="CEO", company="Acme"),
            Contact.create("hiring_manager", "Bo Lead", title="Engineering Manager, Inference Platform", company="Acme"),
            Contact.create("hiring_manager", "Cy Grow", title="Engineering Manager, Growth", company="Acme"),
            Contact.create("recruiter", "Di Hire", title="Technical Recruiter", company="Acme")]


def test_an_inference_role_puts_the_inference_leader_first_with_a_reason():
    ranked = leaders.rank(people(), "Inference Engineer", "j1")
    assert ranked[0].name == "Bo Lead" and "inference" in ranked[0].reason and ranked[0].confidence > ranked[1].confidence
    assert all(c.related_job == "j1" and c.evidence and c.last_verified for c in ranked)
    assert "an inference role" in ranked[0].reason


def test_research_uses_free_search_only_and_never_enriches(monkeypatch):
    monkeypatch.setattr(apollo, "configured", lambda: True)
    asked = []
    monkeypatch.setattr(apollo, "search", lambda company, role, limit, client=None: asked.append(role) or people())
    monkeypatch.setattr(apollo, "enrich", lambda *a, **k: pytest.fail("research must never spend an enrichment credit"))
    out = leaders.research("j1", "Acme", "Inference Engineer")
    assert set(asked) == {"hiring_manager", "ai_leadership", "recruiter"}
    assert out["contacts"][0]["name"] == "Bo Lead" and not any(c["email"] for c in out["contacts"])   # no guessed email
    assert len(out["contacts"]) == 4                                                               # deduplicated


def test_without_apollo_research_says_so_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(apollo, "configured", lambda: False)
    out = leaders.research("j1", "Acme", "ML Engineer")
    assert out["contacts"] == [] and "not set up" in out["errors"][0]


def test_a_linkedin_note_is_checked_and_can_never_be_sent(monkeypatch):
    assert writer.linkedin_problems("Hi Bo, " + "x" * 400, "Bo")[0].startswith("the note was")
    assert any("link" in p for p in writer.linkedin_problems("Hi Bo, see https://x.com", "Bo"))
    assert writer.linkedin_problems("Hi Bo, I built a copilot serving 600 queries a week. Open to connecting?", "Bo") == []
    from fastapi.testclient import TestClient
    from server import outreach as routes
    from server.app import app
    contact = Contact.create("hiring_manager", "Bo Lead", title="EM", company="Acme", email="bo@acme.com")
    store.add_contacts("j1", [contact])
    draft = Draft.create(contact_id=contact.id, subject="LinkedIn connection note", body="Hi Bo, ...")
    draft.channel = "linkedin"
    store.put_draft("j1", draft)
    monkeypatch.setattr(routes, "_job_and_dir", lambda job_id: (None, None))
    r = TestClient(app).post(f"/review/j1/outreach/draft/{draft.id}/send", json={"confirmed": True, "verified_by_user": True})
    assert r.status_code == 409 and "never sends" in r.json()["detail"]


def test_a_field_cto_is_sales_and_a_regional_leader_comes_after_the_us_line():
    cs = [Contact.create("leadership", "F", title="Field CTO, APAC", company="X"),
          Contact.create("leadership", "R", title="CTO, Acme India", company="X"),
          Contact.create("leadership", "C", title="Chief Technology Officer", company="X")]
    ranked = leaders.rank(cs, "Machine Learning Engineer")
    assert ranked[0].name == "C" and "another region" in next(c for c in ranked if c.name == "R").reason
    assert "outside the engineering line" in next(c for c in ranked if c.name == "F").reason
