"""Replies in, positive ones on Slack, follow-ups drafted, nothing sent.

The inbox is a fake IMAP server, Slack a list, the models stubs: what is
tested is matching, the alert's shape, the follow-up schedule, and that a
follow-up goes out in the same thread when the person sends it."""
import email.message
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import mailing
from archive import store as archive
from outreach import replies, slack, store, watch
from outreach.models import Contact, Draft, Reply
from server import queue
from server.models import Job, Status
from tailor import jev, llm
from tailor import outreach as writer
from tailor import replies as judge


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    qpath = tmp_path / "queue.json"
    monkeypatch.setattr(queue, "QUEUE_PATH", qpath)
    monkeypatch.setattr(queue, "LOCK_PATH", qpath.with_suffix(".lock"))
    monkeypatch.setattr(archive, "APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(archive, "INDEX_PATH", tmp_path / "applications" / "index.csv")
    monkeypatch.setattr(store, "STATE", tmp_path / "outreach.json")
    monkeypatch.setattr(store, "CACHE", tmp_path / "outreach_cache.json")
    for name in (jev.API_KEY_ENV, "JEV_API_KEY", slack.WEBHOOK_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(replies.USER_ENV, "me@gmail.com")
    monkeypatch.setenv(replies.PASS_ENV, "app password")


def ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat(timespec="seconds")


def job_with_sent(tmp_path, sent_days=3):
    job, _ = queue.add(Job(url="https://jobs.example.com/7", title="Senior Software Engineer - Python",
                           company="Globex", status=Status.FILLED))
    folder = tmp_path / "applications" / job.folder_name()
    folder.mkdir(parents=True)
    (folder / "resume.pdf").write_bytes(b"%PDF")
    queue.update(job.id, app_dir=str(folder))
    morgan = Contact.create("hiring_manager", "Morgan Reyes", title="Senior Director of Engineering",
                            company="Globex", email="morgan@globex.com", verification="verified", source="apollo")
    store.add_contacts(job.id, [morgan])
    first = Draft.create(morgan.id, "Morgan, I would love to join Globex", "Hi Morgan, ...",
                         status="sent", message_id="<first@me>", sent_at=ago(days=sent_days))
    store.put_draft(job.id, first)
    return queue.get(job.id), folder, morgan, first


# ---------------------------------------------------------------- inbox --
class FakeImap:
    def __init__(self, messages):
        self.messages, self.calls = messages, []

    def select(self, box, readonly=False):
        self.calls.append(("select", box, readonly))
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        return "OK", [" ".join(str(i + 1) for i in range(len(self.messages))).encode()]

    def fetch(self, number, what):
        self.calls.append(("fetch", what))
        msg = self.messages[int(number) - 1]
        return "OK", [(b"1", msg.as_bytes())]


def mail(msg_id, in_reply_to="", body="Hi Pavan, thanks for reaching out.", sender="Morgan <morgan@globex.com>"):
    m = email.message.EmailMessage()
    m["Message-ID"], m["From"], m["Subject"] = msg_id, sender, "Re: Morgan, I would love to join Globex"
    m["Date"] = "Tue, 21 Oct 2026 08:55:00 -0700"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    m.set_content(body)
    return m


def test_only_answers_to_our_mail_are_read_and_nothing_is_marked_read():
    body = ("Hi Pavan, Thanks for reaching out. I am forwarding your resume to the hiring team.\n\n"
            "On Mon, Oct 19, 2026 at 2:22 PM Pavan wrote:\n> I hope this catches you at a better time")
    imap = FakeImap([mail("<news@x>"), mail("<r1@globex>", "<first@me>", body)])
    found = replies.find({"<first@me>"}, imap=imap)
    assert [f.message_id for f in found] == ["<r1@globex>"]
    assert found[0].answers == "<first@me>" and found[0].text.endswith("forwarding your resume to the hiring team.")
    assert ("select", "INBOX", True) in imap.calls
    assert all("PEEK" in call[1] for call in imap.calls if call[0] == "fetch")
    assert sum(1 for c in imap.calls if c == ("fetch", "(BODY.PEEK[])")) == 1   # one body opened: ours


# ---------------------------------------------------------------- slack --
def test_the_alert_reads_like_the_screenshot():
    text = slack.message(candidate="you@example.com", name="Morgan Reyes", title="Senior Director of Engineering",
                         company="Globex", role="Senior Software Engineer- Python",
                         subject="Morgan, I would love to join Globex", replied_to="Follow-up 1",
                         sent_at="2026-10-19T14:55:00+00:00", received_at="2026-10-21T08:55:00+00:00",
                         reply="Hi Pavan, thanks for reaching out. I am forwarding your resume.",
                         why="Forwarding resume to hiring team shows active interest and concrete next step.", win=122)
    lines = text.splitlines()
    assert lines[0] == ":tada: Positive reply for you@example.com — from Morgan Reyes · Senior Director of Engineering @ *Globex*"
    assert [line.split(":*")[0] for line in lines[1:]] == ["*Role", "*Subject", "*Replied to", "*Reply time", "*Reply", "*Why", "*Win #"]
    assert "· 1d 18h after Follow-up 1" in lines[4] and lines[-1] == "*Win #:* 122 all-time"


# ---------------------------------------------------------------- judge --
def test_jev_decides_the_verdict_and_the_text_model_writes_why(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "key")
    monkeypatch.setattr(jev, "ask", lambda state, questions, **k: {
        "verdict": {"type": "choice", "choice": "positive", "confidence": 0.93, "probabilities": {}}})
    monkeypatch.setattr(llm, "complete", lambda system, user, **k:
                        '```json\n{"verdict": "negative", "why": "Forwarding resume to hiring team."}\n```')
    v = judge.classify("I am forwarding your resume to the hiring team.", "SWE at Globex")
    assert (v.verdict, v.why, v.confidence) == ("positive", "Forwarding resume to hiring team.", 0.93)


# -------------------------------------------------------------- watcher --
def test_a_reply_is_recorded_once_and_announced_once_with_its_win_number(tmp_path, monkeypatch):
    job, _, morgan, first = job_with_sent(tmp_path)
    found = replies.Found("<r1@globex>", "<first@me>", "Morgan <morgan@globex.com>", "Re: ...",
                          ago(hours=1), "I am forwarding your resume to the hiring team.")
    own = replies.Found("<mine@me>", "<first@me>", "Me <me@gmail.com>", "Re: ...", ago(hours=1), "my own copy")
    classify = lambda text, role: judge.Verdict("positive", "Forwarding resume to hiring team shows interest.")
    assert watch.check_replies(store.all_jobs(), find=lambda ids, since: [found, own], classify=classify) == 1
    assert watch.check_replies(store.all_jobs(), find=lambda ids, since: [found], classify=classify) == 0
    [reply] = store.get(job.id).replies
    assert reply.verdict == "positive" and reply.win == 1 and reply.contact_id == morgan.id
    posts = []
    assert watch.announce(store.all_jobs(), post=posts.append) == 1
    assert watch.announce(store.all_jobs(), post=posts.append) == 0
    assert "*Replied to:* First email" in posts[0] and "*Win #:* 1 all-time" in posts[0]


def test_follow_ups_wait_two_days_stop_at_two_and_stop_on_any_reply(tmp_path):
    job, _, morgan, first = job_with_sent(tmp_path, sent_days=1)
    row = store.get(job.id)
    assert watch.due_followups(row) == []                                      # one day: too soon
    later = datetime.now(timezone.utc) + timedelta(days=2)
    assert [(d.id, n) for d, n in watch.due_followups(row, later)] == [(first.id, 1)]
    pending = Draft.create(morgan.id, "Re: x", "y", followup=1, reply_to=first.id)
    store.put_draft(job.id, pending)
    assert watch.due_followups(store.get(job.id), later) == []                 # one already waits on you
    store.put_draft(job.id, pending.model_copy(update={"status": "sent", "sent_at": ago(days=3), "message_id": "<f1@me>"}))
    second = store.get(job.id).drafts[-1]
    store.put_draft(job.id, Draft.create(morgan.id, "Re: x", "z", followup=2, reply_to=second.id,
                                         status="sent", sent_at=ago(days=3), message_id="<f2@me>"))
    assert watch.due_followups(store.get(job.id), later) == []                 # two follow-ups is the cap
    row = store.get(job.id)
    row.drafts = row.drafts[:2]
    row.replies = [Reply(id="<r@x>", contact_id=morgan.id, draft_id=first.id, received_at=ago(hours=1))]
    store.save(row)
    assert watch.due_followups(store.get(job.id), later) == []                 # they answered


def test_a_due_follow_up_is_written_as_a_draft_in_the_thread(tmp_path, monkeypatch):
    job, folder, morgan, first = job_with_sent(tmp_path)
    seen = {}

    def followup(context, contact, thread, n, name, brief=None, model=None):
        seen.update(thread=[d.id for d in thread], n=n)
        return writer.Written("Re: Morgan, I would love to join Globex", "Hi Morgan, ...", "test/model", 1)

    monkeypatch.setattr(writer, "write_followup", followup)
    monkeypatch.setattr(watch.drafting, "context", lambda app_dir: object())
    monkeypatch.setattr(watch.drafting, "candidate_name", lambda app_dir: "Pavan")
    assert watch.write_followups(store.all_jobs()) == 1
    draft = store.get(job.id).drafts[-1]
    assert (draft.status, draft.followup, draft.reply_to, draft.attach_resume) == ("draft", 1, first.id, False)
    assert seen == {"thread": [first.id], "n": 1}
    assert watch.write_followups(store.all_jobs()) == 0                        # waits on the person now


def test_sending_a_follow_up_threads_it_under_the_first_email(tmp_path, monkeypatch):
    from server.app import app
    job, folder, morgan, first = job_with_sent(tmp_path)
    follow = Draft.create(morgan.id, "Re: Morgan, I would love to join Globex", "Hi Morgan, a follow-up.",
                          followup=1, reply_to=first.id, attach_resume=False)
    store.put_draft(job.id, follow)
    sent = []
    monkeypatch.setattr(mailing, "send_message", lambda msg: sent.append(msg) or str(msg["Message-ID"]))
    res = TestClient(app).post(f"/review/{job.id}/outreach/draft/{follow.id}/send", json={"confirmed": True})
    assert res.status_code == 200, res.text
    assert sent[0]["In-Reply-To"] == "<first@me>" and sent[0]["References"] == "<first@me>"


def test_follow_up_rules():
    good = ("Hi Morgan,\n\nOn the Senior Software Engineer role at Globex: I also cut p95 latency by 28% on a "
            "production inference service, which matches the reliability work the team describes. Are you the "
            "right person to ask, or could you point me to the hiring team?\n\nBest,\nPavan")
    assert writer.followup_problems(good) == []
    assert any("guilt" in p for p in writer.followup_problems(good.replace("On the", "Just following up on the")))
    assert any("guilt" in p for p in writer.followup_problems(good.replace("On the", "I wanted to follow up on my note about the")))
