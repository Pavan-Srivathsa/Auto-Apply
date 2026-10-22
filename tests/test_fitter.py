"""Every listed role gets its fit by itself: the background checker, its
cache by link, unreadable postings, and fits surviving a list rewrite."""
import pytest

from scout import Posting, Watch, fitter, providers, run, store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(store.ENV, str(tmp_path / "scout.json"))
    monkeypatch.setenv(run.ENV, "0")
    monkeypatch.setenv("AUTOPILOT_FIT_CACHE", str(tmp_path / "fit_cache.json"))
    monkeypatch.setattr(run, "_screen", lambda posting, company: ("ok", "fits"))


def rows(*ids):
    return [Posting(id=str(i), title=f"Software Engineer {i}", url=f"https://x/{i}", location="Austin, TX",
                    posted=f"2026-10-{10 + i:02d}") for i in ids]


def held(monkeypatch, listed):
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: listed)
    watch, _ = store.add_watch(Watch(url="https://boards.greenhouse.io/acme", company="Acme", provider="greenhouse",
                                     args={"slug": "acme", "hold": True}))
    run.check(watch)
    return watch


def test_every_listed_role_gets_a_fit_newest_first_and_each_link_once(monkeypatch):
    watch = held(monkeypatch, rows(1, 2, 3))
    asked = []
    monkeypatch.setattr(run, "_fit", lambda posting, company: asked.append(posting.url) or
                        {"verdict": "qualified", "summary": "Qualified: x"})
    assert [r[2]["id"] for r in fitter.pending()] == ["3", "2", "1"]
    assert fitter.one_pass(workers=1, batch=2) == 2 and asked == ["https://x/3", "https://x/2"]
    assert fitter.one_pass(workers=1) == 1 and fitter.pending() == []
    other, _ = store.add_watch(Watch(url="https://www.lennysjobs.com/", company="Lenny's", provider="lennys"))
    store.update_watch(other.id, listed=[run.listed_row(rows(2)[0])])
    assert fitter.one_pass(workers=1) == 1 and len(asked) == 3                 # the same link: from the cache


def test_a_failed_model_step_writes_nothing_and_is_tried_again(monkeypatch):
    held(monkeypatch, rows(1))
    calls = []
    monkeypatch.setattr(run, "_fit", lambda posting, company: calls.append(1) or {})
    assert fitter.one_pass(workers=1) == 0
    [watch] = store.watches()
    assert "fit" not in watch.listed[0] and fitter.cached("https://x/1") is None
    fitter.one_pass(workers=1)
    assert len(calls) == 2                                                     # tried again, not marked unknown


def test_the_free_steps_decide_before_any_model(monkeypatch):
    from tailor import fetch, profile
    monkeypatch.setattr(profile, "pick", lambda text: pytest.fail("no model call for a ruled-out role"))
    monkeypatch.setattr(fetch, "fetch", lambda url: (_ for _ in ()).throw(RuntimeError("502 Bad Gateway")))
    assert run.fit_or_raise(rows(1)[0], "Intel")["verdict"] == "unknown"       # unreadable page
    senior = rows(2)[0]
    senior.text = "You bring 7+ years of experience building distributed systems."
    assert run.fit_or_raise(senior, "Acme")["summary"] == "Not qualified: asks for 7+ years of experience"


def test_a_fit_written_during_a_check_survives_the_lists_rewrite(monkeypatch):
    watch = held(monkeypatch, rows(1, 2))
    store.set_listed_fit(watch.id, "1", {"verdict": "stretch", "summary": "Stretch: x"})
    run.check(watch)                                     # the stale watch object: the store's fits still win
    row = next(r for r in store.get_watch(watch.id).listed if r["id"] == "1")
    assert row["fit"]["verdict"] == "stretch"
