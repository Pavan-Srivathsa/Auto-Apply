"""Lenny's Jobs as a Scout source and the outreach it starts. The page read
itself is exercised live (the board is a client-rendered site); what is
tested here is everything around it: the rows turned into postings, the
watch that names each company, the source stamp on the queued job, and
that outreach writes drafts and never sends."""
import pytest

import mailing
import pipeline
from archive import store as archive
from outreach import apollo, drafting
from outreach import store as outreach_store
from outreach.models import Contact, Draft
from scout import Posting, Watch, detect, lennys, providers, run, store
from server import outreach as outreach_api
from server import postings, queue, runner, settings
from server.models import Job, Status
from tailor import outreach as writer


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(store.ENV, str(tmp_path / "scout.json"))
    monkeypatch.setenv(run.ENV, "0")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    qpath = tmp_path / "queue.json"
    monkeypatch.setattr(queue, "QUEUE_PATH", qpath)
    monkeypatch.setattr(queue, "LOCK_PATH", qpath.with_suffix(".lock"))
    monkeypatch.setattr(runner, "start_pipeline", lambda job_id, log_dir=None: 4242)
    monkeypatch.setenv(postings.DIR_ENV, str(tmp_path / "postings"))
    monkeypatch.setattr(run, "_screen", lambda posting, company: ("ok", "fits"))
    monkeypatch.setattr(archive, "APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(archive, "INDEX_PATH", tmp_path / "applications" / "index.csv")
    monkeypatch.setattr(outreach_store, "STATE", tmp_path / "outreach.json")
    monkeypatch.setattr(outreach_store, "CACHE", tmp_path / "outreach_cache.json")
    monkeypatch.delenv(apollo.API_KEY, raising=False)


ROWS = [
    {"href": "https://jobs.ashbyhq.com/acme/1?utm_source=lennysjobs&utm_medium=job_board&ref=lennysjobs",
     "title": "Software Engineer, New Grad", "company": "Acme", "location": "SAN FRANCISCO", "age": "2 days"},
    {"href": "https://boards.greenhouse.io/beta/jobs/9?gh_jid=9&utm_source=lennysjobs&utm_medium=job_board&ref=lennysjobs",
     "title": "Backend Engineer", "company": "Beta", "location": "REMOTE US", "age": "today"},
    {"href": "https://jobs.ashbyhq.com/acme/1?utm_source=lennysjobs&utm_medium=job_board&ref=lennysjobs",
     "title": "Software Engineer, New Grad", "company": "Acme", "location": "SAN FRANCISCO", "age": "2 days"},
]


def test_the_boards_tracking_tags_come_off_the_employer_link():
    assert lennys.clean_url(ROWS[0]["href"]) == "https://jobs.ashbyhq.com/acme/1"
    # The id the employer's own form needs stays.
    assert lennys.clean_url(ROWS[1]["href"]) == "https://boards.greenhouse.io/beta/jobs/9?gh_jid=9"


def test_an_age_becomes_a_date():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    assert lennys.posted_date("2 days", now) == "2026-10-19"
    assert lennys.posted_date("1 week", now) == "2026-10-14"
    assert lennys.posted_date("today", now) == "2026-10-21"
    assert lennys.posted_date("soon", now) == ""


def test_rows_become_postings_once_each_newest_first_with_their_company():
    rows = lennys.to_postings(ROWS)
    assert [p.company for p in rows] == ["Beta", "Acme"]        # today before 2 days; the repeat dropped
    assert rows[1].url == rows[1].id == "https://jobs.ashbyhq.com/acme/1"
    assert rows[0].location == "REMOTE US"


def test_a_lennys_url_is_a_watch_on_the_engineering_filter():
    provider, args, company = detect("https://www.lennysjobs.com/")
    assert provider == "lennys" and company == "Lenny's Jobs"
    assert args["category"] == "Engineering (Software)"
    assert "1 - Entry & mid-level" in args["levels"] and args["posted"] == "week"
    assert providers.PROVIDERS["lennys"] is lennys.fetch


def test_a_check_queues_each_role_under_its_own_company_marked_lennys(monkeypatch):
    watch, _ = store.add_watch(Watch(url=lennys.URL, company="Lenny's Jobs", provider="lennys", args={}))
    seed = [lennys.to_postings(ROWS)[1]]               # Acme is already known
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: seed)
    run.check(watch)                                    # the first read only records what is there
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: lennys.to_postings(ROWS))
    result = run.check(store.get_watch(watch.id))
    assert result["new"] == 1 and result["queued"] == 1
    [job] = queue.all_jobs()
    assert job.company == "Beta" and job.source == "lennys"          # its own company, not "Lenny's Jobs"
    assert job.url == "https://boards.greenhouse.io/beta/jobs/9?gh_jid=9"


def application(tmp_path, source="lennys"):
    job, _ = queue.add(Job(url="https://jobs.example.com/42", title="Software Engineer", company="Acme",
                           status=Status.AWAITING_REVIEW, source=source))
    folder = tmp_path / "applications" / job.folder_name()
    folder.mkdir(parents=True)
    (folder / "resume.pdf").write_bytes(b"%PDF")
    queue.update(job.id, app_dir=str(folder))
    return queue.get(job.id), folder


def contact(role, name, email=""):
    return Contact.create(role=role, name=name, company="Acme", email=email, apollo_id=f"id-{name}", source="apollo")


def stub_drafting(monkeypatch, folder):
    sent = []
    monkeypatch.setattr(mailing, "send_message", lambda message: sent.append(message))
    monkeypatch.setattr(drafting, "candidate_name", lambda app_dir: "Jane Doe")
    monkeypatch.setattr(drafting, "context", lambda app_dir: object())
    monkeypatch.setattr(writer, "draft", lambda ctx, c, title, company, name, attach, folder_name, brief=None:
                        Draft.create(c.id, f"Note for {c.name}", f"Hi {c.name}, a grounded note.", resume_folder=folder.name))
    return sent


def test_outreach_writes_a_draft_per_role_and_sends_nothing(tmp_path, monkeypatch):
    job, folder = application(tmp_path)
    monkeypatch.setenv(apollo.API_KEY, "key")
    found = [contact("recruiter", "Rita", "rita@acme.com"), contact("recruiter", "Ray", "ray@acme.com"),
             contact("hiring_manager", "Morgan", "morgan@acme.com")]
    monkeypatch.setattr(apollo, "discover", lambda company, per_role: found)
    sent = stub_drafting(monkeypatch, folder)
    line = drafting.prepare(job.id)
    state = outreach_store.get(job.id)
    assert len(state.drafts) == 2 and all(d.status == "draft" for d in state.drafts)    # one recruiter, one manager
    assert {d.contact_id for d in state.drafts} == {found[0].id, found[2].id}
    assert sent == [] and "nothing sent" in line
    # Running it again does not write a second set or spend more credits.
    assert "already exist" in drafting.prepare(job.id)


def test_outreach_without_an_apollo_key_does_nothing(tmp_path):
    job, _ = application(tmp_path)
    assert "no Apollo key" in drafting.prepare(job.id)
    assert outreach_store.get(job.id).drafts == []


def test_the_pipeline_prepares_outreach_for_lennys_jobs_only_and_the_switch_stops_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(drafting, "prepare", lambda job_id: calls.append(job_id) or "ok")
    lenny, _ = application(tmp_path)
    other = lenny.model_copy(update={"source": "scout"})
    pipeline.prepare_outreach(other)
    assert calls == []
    pipeline.prepare_outreach(lenny)
    assert calls == [lenny.id]
    settings.save(auto_outreach=False)
    pipeline.prepare_outreach(lenny)
    assert calls == [lenny.id]


def test_an_outreach_failure_never_fails_the_application(tmp_path, monkeypatch):
    def boom(job_id):
        raise RuntimeError("apollo down")
    monkeypatch.setattr(drafting, "prepare", boom)
    lenny, _ = application(tmp_path)
    pipeline.prepare_outreach(lenny)        # does not raise


def test_a_lennys_check_follows_each_company_to_its_own_board(monkeypatch):
    watch, _ = store.add_watch(Watch(url=lennys.URL, company="Lenny's Jobs", provider="lennys", args={"follow": True}))
    store.add_watch(Watch(url="https://jobs.lever.co/gamma", company="Gamma", provider="lever", args={"slug": "gamma"}))
    rows = lennys.to_postings(ROWS + [
        {"href": "https://jobs.lever.co/gamma/5?utm_source=lennysjobs", "title": "SWE", "company": "Gamma", "age": "1 day"},
        {"href": "https://www.amazon.jobs/en/jobs/1/x?utm_source=lennysjobs", "title": "SDE", "company": "Amazon", "age": "1 day"},
        {"href": "https://careers.example.com/jobs/7?utm_source=lennysjobs", "title": "SWE", "company": "Odd", "age": "1 day"},
    ])
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: rows if w.provider == "lennys" else rows[:1])
    run.check(watch)
    boards = {w.provider: w for w in store.watches() if w.provider != "lennys"}
    # Acme (Ashby) and Beta (Greenhouse) are new; Gamma was already watched; Amazon and the odd host are not boards.
    assert sorted(w.company for w in store.watches() if w.args.get("via") == "lennys") == ["Acme", "Beta"]
    assert boards["ashby"].url == "https://jobs.ashbyhq.com/acme" and boards["ashby"].notify is False
    assert len([w for w in store.watches() if w.provider == "lever"]) == 1
    # A role a followed board finds later is a Lenny's job too, so it gets the outreach drafts.
    hit = run.Hit(watch_id=boards["ashby"].id, company="Acme", posting={"id": "9", "url": "https://jobs.ashbyhq.com/acme/9", "title": "SWE"}, verdict="ok")
    store.add_hits([hit])
    run.queue_hit(hit.id)
    assert queue.all_jobs()[-1].source == "lennys"


def test_a_board_that_does_not_read_is_not_followed(monkeypatch):
    def fetch(w, http=None):
        if w.provider == "lennys":
            return lennys.to_postings(ROWS[:1])
        raise RuntimeError("404")
    monkeypatch.setattr(providers, "fetch", fetch)
    watch, _ = store.add_watch(Watch(url=lennys.URL, company="Lenny's Jobs", provider="lennys", args={}))
    run.check(watch)
    assert [w.provider for w in store.watches()] == ["lennys"]


def test_followed_boards_wait_for_the_person_and_skip_what_they_said(monkeypatch):
    watch, _ = store.add_watch(Watch(url=lennys.URL, company="Lenny's Jobs", provider="lennys",
                                     args={"follow": True, "skip": ["beta"]}))
    rows = lennys.to_postings(ROWS)
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: rows if w.provider == "lennys" else rows[:1])
    run.check(watch)
    followed = [w for w in store.watches() if w.args.get("via") == "lennys"]
    assert [w.company for w in followed] == ["Acme"]                       # Beta is on the skip list
    assert followed[0].args["hold"] is True
    acme = followed[0]
    run.check(acme)                                                        # first look: records only
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: rows[:1] + [
        Posting(id="https://jobs.ashbyhq.com/acme/2", title="Software Engineer II", url="https://jobs.ashbyhq.com/acme/2",
                text="desc", company="Acme")])
    result = run.check(store.get_watch(acme.id))
    assert result["new"] == 1 and result["queued"] == 0                   # listed, waiting for the click
    assert queue.all_jobs() == []


def test_a_row_without_location_or_company_reads_them_off_its_link():
    from scout import filter as level
    url = "https://accenture.wd103.myworkdayjobs.com/AccentureCareers/job/Bengaluru/Custom-Software-Engineer_ATCI-1"
    [row] = lennys.to_postings([{"href": url, "title": "Custom Software Engineer", "company": "", "location": ""}])
    assert row.company == "Accenture"
    assert level.place_in_url(url) == "Bengaluru"
    watch = Watch(url=lennys.URL, company="Lenny's Jobs", provider="lennys")
    assert level.matching(watch, [row]) == []                                  # India, dropped before any screen
    assert lennys.company_from_url("https://jobs.ashbyhq.com/traba/1") == "Traba"
    assert level.place_in_url("https://jobs.ashbyhq.com/traba/1") == ""


def test_a_skipped_company_is_never_listed_or_queued_from_lennys_itself(monkeypatch):
    watch, _ = store.add_watch(Watch(url=lennys.URL, company="Lenny's Jobs", provider="lennys",
                                     args={"follow": False, "skip": ["beta"]}))
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: [])
    seed = lennys.to_postings(ROWS)[1:]                                       # Acme known
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: seed)
    run.check(watch)
    monkeypatch.setattr(providers, "fetch", lambda w, http=None: lennys.to_postings(ROWS))
    result = run.check(store.get_watch(watch.id))
    assert result["new"] == 0 and result["matching"] == 1                     # Beta skipped, Acme already seen
    assert [r["title"] for r in store.get_watch(watch.id).listed] == ["Software Engineer, New Grad"]
