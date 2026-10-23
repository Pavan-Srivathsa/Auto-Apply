"""Listing history: closing needs two complete listings without the role, a
failed read closes nothing, a repost needs time, a parallel opening is not
a repost, a partial provider never closes, and age alone is not staleness."""
import pytest

from scout import Posting, Watch, history


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_SCOUT_HISTORY", str(tmp_path / "history.json"))


def p(pid, title="ML Engineer", location="SF", text=""):
    return Posting(id=pid, title=title, url=f"https://jobs/{pid}", location=location, text=text)


W = Watch(url="https://boards.greenhouse.io/acme", company="Acme", provider="greenhouse")


def test_a_role_closes_only_after_two_complete_listings_without_it_and_a_failure_closes_nothing():
    history.reconcile(W, [p("1"), p("2", "Data Engineer")], "2026-09-01T00:00:00+00:00")
    history.failure(W, "timeout", "2026-09-02T00:00:00+00:00")
    out = {r["id"]: r for r in history.records(W.id)}
    assert out["1"]["state"] == history.NEW and out["1"]["failures"] == 1
    history.reconcile(W, [p("2", "Data Engineer")], "2026-09-03T00:00:00+00:00")
    assert {r["id"]: r for r in history.records(W.id)}["1"]["state"] != history.CLOSED     # once is not enough
    history.reconcile(W, [p("2", "Data Engineer")], "2026-09-04T00:00:00+00:00")
    assert {r["id"]: r for r in history.records(W.id)}["1"]["state"] == history.CLOSED


def test_a_repost_needs_a_closed_twin_and_time_and_a_parallel_opening_is_not_one():
    history.reconcile(W, [p("1"), p("9", "Data Engineer")], "2026-09-01T00:00:00+00:00")
    history.reconcile(W, [p("9", "Data Engineer")], "2026-09-02T00:00:00+00:00")
    history.reconcile(W, [p("9", "Data Engineer")], "2026-09-03T00:00:00+00:00")         # 1 closed
    out = history.reconcile(W, [p("9", "Data Engineer"), p("7", "Engineer, ML")], "2026-09-20T00:00:00+00:00")
    assert out["7"]["state"] == history.REPOSTED and out["7"]["repost_of"] == "1"        # same words, another order
    both = history.reconcile(W, [p("9", "Data Engineer"), p("7", "Engineer, ML"), p("8", "ML Engineer")],
                             "2026-09-21T00:00:00+00:00")
    assert both["8"]["state"] == history.NEW and "parallel" in both["8"]["transitions"][-1]["why"]
    city = history.reconcile(W, [p("9", "Data Engineer"), p("7", "Engineer, ML"), p("8", "ML Engineer"),
                                 p("5", "ML Engineer Berlin")], "2026-09-22T00:00:00+00:00")
    assert city["5"]["state"] == history.NEW                                            # another title


def test_a_partial_provider_never_closes_and_age_alone_is_not_staleness():
    yc = Watch(url="https://www.ycombinator.com/jobs", company="YC", provider="yc")
    history.reconcile(yc, [p("1")], "2026-09-01T00:00:00+00:00")
    for day in ("02", "03", "04"):
        history.reconcile(yc, [p("2", "Other")], f"2026-09-{day}T00:00:00+00:00")
    assert {r["id"]: r for r in history.records(yc.id)}["1"]["state"] == history.UNVERIFIED
    old = history.reconcile(W, [p("3")], "2026-06-01T00:00:00+00:00")
    old = history.reconcile(W, [p("3")], "2026-09-01T00:00:00+00:00")
    assert old["3"]["state"] == history.ACTIVE and history.freshness(old["3"], "2026-09-01T00:00:00+00:00")["label"] == "open"


def test_the_same_job_at_a_new_url_is_recognised_in_the_same_check():
    text = "We build inference systems. " * 20
    history.reconcile(W, [p("1", text=text)], "2026-09-01T00:00:00+00:00")
    out = history.reconcile(W, [p("2", text=text)], "2026-09-05T00:00:00+00:00")
    assert out["2"]["state"] == history.REPOSTED and "new identifier" in out["2"]["transitions"][-1]["why"]
