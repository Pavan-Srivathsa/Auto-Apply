"""The ML/DS bullet reviewer: provenance, claims checked against a bullet's
own source, Jev's verdicts against thresholds, escalation, the cache, the
judge being unavailable, the controlled revision, and the approval gate.

No network: Jev and the reasoning model are stubbed, and the review cache
lives in the test's own folder."""
import json

import httpx
import pytest

from server.models import Job, Status
from tailor import costs, jev, llm, reviewer

MASTER = r"""\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading{AI Engineer}{Acme}{LA}{2024}
\resumeItem{Benchmarked vLLM and TGI on A100 GPUs, measuring 410 ms p95 latency}
\resumeItem{Built Airflow DAGs moving 2M rows a day into Snowflake}
\resumeItem{Contributed to a team of five building a fraud service in PySpark}
\end{document}
"""


def _pipeline():
    """The pipeline imports browser/; the reviewer never does. A test that
    needs the pipeline skips when it cannot import, and the rest still run."""
    return pytest.importorskip("pipeline")


def tailored(*bullets):
    out = MASTER
    for old, new in bullets:
        out = out.replace(old, new)
    return out


def good_scores(**low):
    row = {name: 0.95 for name in reviewer.QUESTIONS} | {"relevance": 1.5, "readability": 1.5}
    return row | low


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_REVIEW_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setattr(reviewer, "THRESHOLDS", tmp_path / "none.json")     # the defaults, not the calibration
    for name in ("JEV_API_KEY", jev.API_KEY_ENV, "OPENROUTER_REVIEW_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reviewer, "llm_configured", lambda: False)


def with_jev(monkeypatch, rows):
    """Jev configured and answering `rows(pairs)` -> one score dict per pair."""
    monkeypatch.setenv("JEV_API_KEY", "key")
    calls = []

    def scores(batch, requirements):
        calls.append([p.rewritten for p in batch])
        return [rows(p) for p in batch]
    monkeypatch.setattr(reviewer, "_jev_scores", scores)
    return calls


# ------------------------------------------------------------- provenance --
def test_a_reordered_bullet_keeps_its_own_source():
    swapped = MASTER.replace(
        r"\resumeItem{Benchmarked vLLM and TGI on A100 GPUs, measuring 410 ms p95 latency}"
        "\n" r"\resumeItem{Built Airflow DAGs moving 2M rows a day into Snowflake}",
        r"\resumeItem{Built Airflow DAGs that move 2M rows a day into Snowflake}"
        "\n" r"\resumeItem{Benchmarked vLLM and TGI on A100 GPUs, measuring 410 ms p95 latency}")
    found = reviewer.pairs(MASTER, swapped)
    assert "Airflow" in found[0].rewritten and "Airflow" in found[0].source
    assert "vLLM" in found[1].source


def test_a_figure_elsewhere_on_the_master_does_not_support_this_bullet():
    # 410 is on the master, under another bullet: not this bullet's support.
    c = reviewer.claims("Built Airflow DAGs moving 2M rows a day at 410 ms", "Built Airflow DAGs moving 2M rows a day")
    assert c["unverified_figures"] == ["410"] and "2M" in c["verified"]


def test_a_story_swap_is_judged_against_that_storys_lines():
    story = "Summary: research\nNumbers: 0.89 macro F1 on 22 classes with XLNet\nStack: XLNet, PyTorch"
    swapped = tailored(("Contributed to a team of five building a fraud service in PySpark",
                        "Classified 22 intent classes with XLNet at 0.89 macro F1"))
    found = reviewer.pairs(MASTER, swapped, {"research-intern-usc": story})
    third = found[2]
    assert third.source_kind == "story:research-intern-usc" and "0.89" in third.source
    assert reviewer.claims(third.rewritten, third.source)["unverified"] == []


# ---------------------------------------------------------------- verdicts --
def test_a_correct_paraphrase_passes(monkeypatch):
    with_jev(monkeypatch, lambda p: good_scores())
    r = reviewer.review(MASTER, tailored(("Benchmarked vLLM and TGI", "Compared vLLM and TGI")))
    changed = [b for b in r.bullets if b.method != "unchanged"]
    assert [b.verdict for b in changed] == [reviewer.PASS] and not r.needs_human


def test_an_invented_figure_fails_whatever_jev_says(monkeypatch):
    with_jev(monkeypatch, lambda p: good_scores())
    r = reviewer.review(MASTER, tailored(("measuring 410 ms p95 latency", "measuring 41 ms p95 latency")))
    bad = next(b for b in r.bullets if "41 ms" in b.rewritten)
    assert bad.verdict == reviewer.FAIL and bad.issue_type == "unverified figure" and r.needs_human


def test_a_semantic_change_with_no_new_token_is_left_to_jev(monkeypatch):
    # "Benchmarked" -> "Optimized": nothing new for the code to see.
    with_jev(monkeypatch, lambda p: good_scores(technical=0.12) if "Optimized" in p.rewritten else good_scores())
    r = reviewer.review(MASTER, tailored(("Benchmarked vLLM and TGI", "Optimized vLLM and TGI")))
    bad = next(b for b in r.bullets if "Optimized" in b.rewritten)
    assert bad.verdict == reviewer.FAIL and bad.issue_type == reviewer.ISSUES["technical"]


def test_an_unsure_jev_is_a_warning_and_never_a_pass(monkeypatch):
    with_jev(monkeypatch, lambda p: good_scores(ownership=0.6))
    r = reviewer.review(MASTER, tailored(("Contributed to a team of five building", "Helped a team of five build")))
    b = next(b for b in r.bullets if "Helped" in b.rewritten)
    assert b.verdict == reviewer.WARN and r.needs_human


def test_without_a_judge_a_changed_bullet_is_unknown_and_blocks(monkeypatch):
    r = reviewer.review(MASTER, tailored(("Benchmarked vLLM and TGI", "Compared vLLM and TGI")))
    b = next(b for b in r.bullets if "Compared" in b.rewritten)
    assert b.verdict == reviewer.UNKNOWN and "not configured" in b.issue and r.needs_human


def test_a_jev_failure_is_unknown_not_a_pass(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "key")
    monkeypatch.setattr(reviewer, "_jev_scores", lambda batch, req: (_ for _ in ()).throw(jev.JevError("503")))
    r = reviewer.review(MASTER, tailored(("Benchmarked vLLM and TGI", "Compared vLLM and TGI")))
    b = next(b for b in r.bullets if "Compared" in b.rewritten)
    assert b.verdict == reviewer.UNKNOWN and "Jev unavailable" in b.issue


def test_an_unchanged_resume_needs_no_judge():
    r = reviewer.review(MASTER, MASTER)
    assert {b.verdict for b in r.bullets} == {reviewer.PASS} and not r.needs_human


# -------------------------------------------------------------- escalation --
def test_a_warning_is_escalated_alone_and_the_evidence_must_be_a_quote(monkeypatch):
    with_jev(monkeypatch, lambda p: good_scores(ownership=0.6) if "Helped" in p.rewritten else good_scores())
    monkeypatch.setattr(reviewer, "llm_configured", lambda: True)
    asked = []

    def complete(system, user, **kw):
        asked.append(user)
        return '```json\n{"verdict": "FAIL", "issue_type": "inflated ownership", ' \
               '"issue": "claims more than contributed", "evidence": "a quote that is not there"}\n```'
    monkeypatch.setattr(reviewer.llm, "complete", complete)
    r = reviewer.review(MASTER, tailored(("Contributed to a team of five building", "Helped a team of five build"),
                                         ("Benchmarked vLLM and TGI", "Compared vLLM and TGI")))
    b = next(b for b in r.bullets if "Helped" in b.rewritten)
    assert len(asked) == 1 and "Helped" in asked[0]                 # only the unsure bullet
    assert b.verdict == reviewer.FAIL and b.escalated and b.evidence == b.source   # the made-up quote is dropped


def test_a_malformed_escalation_leaves_the_warning(monkeypatch):
    with_jev(monkeypatch, lambda p: good_scores(ownership=0.6))
    monkeypatch.setattr(reviewer, "llm_configured", lambda: True)
    monkeypatch.setattr(reviewer.llm, "complete", lambda *a, **k: "I think it is fine.")
    r = reviewer.review(MASTER, tailored(("Contributed to a team of five building", "Helped a team of five build")))
    b = next(b for b in r.bullets if "Helped" in b.rewritten)
    assert b.verdict == reviewer.WARN and "escalation failed" in b.issue


# ------------------------------------------------------------------- cache --
def test_an_unchanged_decision_is_not_asked_twice_and_the_rubric_is_in_the_key(monkeypatch):
    calls = with_jev(monkeypatch, lambda p: good_scores())
    resume = tailored(("Benchmarked vLLM and TGI", "Compared vLLM and TGI"))
    reviewer.review(MASTER, resume)
    second = reviewer.review(MASTER, resume)
    assert len(calls) == 1 and any("(cached)" in b.method for b in second.bullets)
    monkeypatch.setattr(reviewer, "RUBRIC_VERSION", "mlds-next")
    reviewer.review(MASTER, resume)
    assert len(calls) == 2


# ---------------------------------------------------------------- the Jev client --
def answer(question_type="noul", value=0.7):
    key = "noul" if question_type == "noul" else "score"
    return {"answers": {"q": {"type": question_type, key: value}}, "usage": {"input_tokens": 1000, "output_tokens": 5}}


def mock(responses, monkeypatch):
    queue = list(responses)
    seen = []

    def handler(request):
        seen.append(request)
        status, body = queue.pop(0)
        return httpx.Response(status, json=body)
    monkeypatch.setenv("JEV_API_KEY", "secret-key")
    monkeypatch.setattr(jev, "BACKOFF", (0, 0))
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_jev_retries_a_transient_failure_and_counts_the_cost(monkeypatch):
    client, seen = mock([(503, {}), (200, answer())], monkeypatch)
    with costs.track() as ledger:
        out = jev.ask({"a": "x"}, {"q": jev.noul("?")}, client=client)
    assert out["q"]["noul"] == 0.7 and len(seen) == 2
    assert ledger.lines["jev"].input_tokens == 1000 and ledger.usd == pytest.approx(1000 * 0.042 / 1e6)


def test_jev_refuses_an_answer_out_of_shape(monkeypatch):
    client, _ = mock([(200, answer(value=1.7))], monkeypatch)
    with pytest.raises(jev.JevError, match="not a probability"):
        jev.ask({"a": "x"}, {"q": jev.noul("?")}, client=client)
    client, _ = mock([(200, answer("score", 5))], monkeypatch)
    with pytest.raises(jev.JevError, match="outside"):
        jev.ask({"a": "x"}, {"q": jev.score("?", ["a", "b", "c"])}, client=client)


def test_jev_errors_never_carry_the_key_and_a_huge_state_is_refused(monkeypatch):
    client, _ = mock([(401, {"error": "bad"})] * 3, monkeypatch)
    with pytest.raises(jev.JevError) as caught:
        jev.ask({"a": "x"}, {"q": jev.noul("?")}, client=client)
    assert "secret-key" not in str(caught.value)
    with pytest.raises(jev.JevError, match="at most"):
        jev.ask({"a": "x" * (jev.MAX_STATE + 1)}, {"q": jev.noul("?")}, client=client)


# --------------------------------------------------------- compatibility --
def test_an_old_or_odd_review_file_still_loads(tmp_path):
    assert reviewer.load(tmp_path) is None                       # a folder from before this existed
    (tmp_path / reviewer.FILE).write_text(json.dumps(
        {"rubric": "old", "bullets": [{"index": 0, "original": "a", "rewritten": "b", "source": "a",
                                       "source_kind": "master", "verdict": "PASS", "future_field": 1}]}))
    loaded = reviewer.load(tmp_path)
    assert loaded.bullets[0].verdict == reviewer.PASS and not loaded.needs_human


def test_an_accepted_warning_no_longer_blocks():
    r = reviewer.Review(bullets=[reviewer.BulletReview(0, "a", "b", "a", "master", verdict=reviewer.WARN)])
    assert r.needs_human
    r.bullets[0].decision = "accept"
    assert not r.needs_human


# --------------------------------------------------- controlled revision --
class Base:
    tex_path = pdf_path = None


class Posting:
    text = "We want LLM inference work."


def result_of(tex):
    from tailor.tailor import TailorResult
    return TailorResult(tex=tex, diff="", suggestions="", model="stub", attempts=1)


def stub_revision(monkeypatch, corrected_tex):
    pipeline = _pipeline()
    monkeypatch.setattr(pipeline, "make_page_check", lambda path: None)
    monkeypatch.setattr(pipeline, "base_page_count", lambda *a: 1)
    asked = []

    def tailor_stub(posting, **kw):
        asked.append(kw["extra_instruction"])
        return result_of(corrected_tex)
    monkeypatch.setattr(pipeline.tailor, "tailor", tailor_stub)
    return asked


def test_a_flagged_bullet_gets_a_correction_and_the_correction_is_checked(tmp_path, monkeypatch):
    pipeline = _pipeline()
    with_jev(monkeypatch, lambda p: good_scores())
    bad = tailored(("measuring 410 ms p95 latency", "measuring 41 ms p95 latency"))
    asked = stub_revision(monkeypatch, tailored(("measuring 410 ms p95 latency", "recording 410 ms p95 latency")))
    out = pipeline.verify_bullets(tmp_path, Posting(), MASTER, result_of(bad), None, "", [], Base(), None)
    review = reviewer.load(tmp_path)
    assert "41 ms" in asked[0] and "410 ms p95 latency" in asked[0]          # the issue and the evidence
    assert "recording 410 ms" in out.tex and review.rounds[0]["outcome"] == "correction kept"
    assert not review.needs_human


def test_a_correction_that_cannot_be_verified_puts_the_master_wording_back(tmp_path, monkeypatch):
    pipeline = _pipeline()
    with_jev(monkeypatch, lambda p: good_scores())
    bad = tailored(("measuring 410 ms p95 latency", "measuring 41 ms p95 latency"))
    stub_revision(monkeypatch, bad)                                            # the writer does not fix it
    out = pipeline.verify_bullets(tmp_path, Posting(), MASTER, result_of(bad), None, "", [], Base(), None)
    review = reviewer.load(tmp_path)
    assert "measuring 410 ms p95 latency" in out.tex and "41 ms" not in out.tex
    assert review.reverted and review.needs_human                              # safe wording, and the person decides


def test_a_bullet_review_that_needs_the_person_blocks_auto_approve(tmp_path, monkeypatch):
    pipeline = _pipeline()
    from server import settings
    monkeypatch.setattr(pipeline, "tell_tab", lambda *a: None)
    monkeypatch.setattr(pipeline.settings, "auto_fill", lambda: True)
    (tmp_path / "screen.json").write_text('{"verdict": "ok"}')
    started = []
    monkeypatch.setattr(pipeline.runner, "start_fill", lambda job_id, log_dir=None: started.append(job_id))
    job = Job(url="https://example.com/1")
    assert not pipeline.auto_approve(tmp_path, job) and not started            # no review at all: waits
    reviewer.save(tmp_path, reviewer.Review(bullets=[reviewer.BulletReview(0, "a", "b", "a", "master",
                                                                           verdict=reviewer.FAIL)]))
    assert not pipeline.auto_approve(tmp_path, job) and not started            # a FAIL: waits


# ------------------------------------------------------------- the page --
@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from archive import store
    from server import queue
    from server.app import app
    qpath = tmp_path / "queue.json"
    monkeypatch.setattr(queue, "QUEUE_PATH", qpath)
    monkeypatch.setattr(queue, "LOCK_PATH", qpath.with_suffix(".lock"))
    monkeypatch.setattr(store, "APPLICATIONS", tmp_path / "applications")
    monkeypatch.setattr(store, "INDEX_PATH", tmp_path / "applications" / "index.csv")
    monkeypatch.setenv("AUTOPILOT_BULLET_DECISIONS", str(tmp_path / "decisions.json"))
    job, _ = queue.add(Job(url="https://example.com/jobs/1", title="ML Engineer", company="Example"))
    app_dir = store.create(job)
    bad = tailored(("measuring 410 ms p95 latency", "measuring 41 ms p95 latency"))
    store.write(app_dir, "resume.tex", bad)
    store.write(app_dir, "master_resume.tex", MASTER)
    store.set_status(app_dir, Status.AWAITING_REVIEW)
    queue.update(job.id, app_dir=str(app_dir), status=Status.AWAITING_REVIEW)
    with_jev(monkeypatch, lambda p: good_scores())
    reviewer.save(app_dir, reviewer.review(MASTER, bad))
    return TestClient(app), job, app_dir


def test_the_page_shows_the_review_and_a_decision_is_kept_outside_the_archive(api):
    client, job, app_dir = api
    detail = client.get(f"/review/{job.id}").json()
    failed = [b for b in detail["bullet_review"]["bullets"] if b["verdict"] == "FAIL"]
    assert failed and detail["bullet_review"]["needs_human"]
    index = failed[0]["index"]
    out = client.post(f"/review/{job.id}/bullets/{index}/decision", json={"decision": "accept"}).json()
    assert not out["review"]["needs_human"]                                   # accepted: no longer blocks
    assert client.post(f"/review/{job.id}/bullets/{index}/decision", json={"decision": "maybe"}).status_code == 422
    assert json.loads((app_dir / reviewer.FILE).read_text())["bullets"][index]["decision"] == ""  # file untouched


def test_rejecting_puts_the_master_wording_back_as_a_new_version(api, monkeypatch, tmp_path):
    pipeline = _pipeline()
    client, job, app_dir = api
    saved = {}

    def save_resume(j, tex):
        from archive import store
        new = store.create(j)
        store.write(new, "resume.tex", tex)
        store.write(new, "master_resume.tex", MASTER)
        saved["tex"] = tex
        return new
    monkeypatch.setattr(pipeline, "save_resume", save_resume)
    index = next(b["index"] for b in client.get(f"/review/{job.id}").json()["bullet_review"]["bullets"]
                 if b["verdict"] == "FAIL")
    assert client.post(f"/review/{job.id}/bullets/apply").status_code == 409     # nothing rejected yet
    client.post(f"/review/{job.id}/bullets/{index}/decision", json={"decision": "reject"})
    out = client.post(f"/review/{job.id}/bullets/apply").json()
    assert "measuring 410 ms p95 latency" in saved["tex"] and "41 ms" not in saved["tex"]
    assert out["restored"] == 1 and not out["review"]["needs_human"]


def test_an_edit_is_escaped_saved_and_never_overwritten_by_a_later_revision(api, monkeypatch, tmp_path):
    pipeline = _pipeline()
    client, job, app_dir = api
    monkeypatch.setenv("AUTOPILOT_BULLET_LOCKS", str(tmp_path / "locks.json"))
    saved = {}

    def save_resume(j, tex):
        from archive import store
        new = store.create(j)
        store.write(new, "resume.tex", tex)
        store.write(new, "master_resume.tex", MASTER)
        saved["tex"] = tex
        return new
    monkeypatch.setattr(pipeline, "save_resume", save_resume)
    index = next(b["index"] for b in client.get(f"/review/{job.id}").json()["bullet_review"]["bullets"]
                 if b["verdict"] == "FAIL")
    assert client.post(f"/review/{job.id}/bullets/{index}/decision", json={"decision": "edit", "text": "x"}).status_code == 422
    mine = "Benchmarked vLLM & TGI on A100 GPUs at 410 ms p95 latency"
    client.post(f"/review/{job.id}/bullets/{index}/decision", json={"decision": "edit", "text": mine})
    out = client.post(f"/review/{job.id}/bullets/apply").json()
    assert r"vLLM \& TGI" in saved["tex"] and out["edited"] == 1                 # escaped for LaTeX
    # A later automated round writes the model's wording again; the lock puts the person's back.
    later = tailored(("measuring 410 ms p95 latency", "measuring 410 ms tail latency"))
    folder = tmp_path / "later"
    folder.mkdir()
    (folder / "job.json").write_text(json.dumps({"id": job.id}))
    stub_revision(monkeypatch, later)
    result = pipeline.verify_bullets(folder, Posting(), MASTER, result_of(later), None, "", [], Base(), None)
    assert r"vLLM \& TGI" in result.tex and "tail latency" not in result.tex


def test_usage_is_counted_per_provider_and_never_carries_text(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_USAGE", str(tmp_path / "usage.json"))
    costs.record("jev", 1000, 10)
    costs.record("openrouter:glm", 500, 200, usd=0.01)
    u = costs.usage()
    assert u["by_provider"]["jev"]["calls"] == 1 and u["usd"] == pytest.approx(0.01 + 1000 * 0.042 / 1e6, abs=1e-4)
    stored = (tmp_path / "usage.json").read_text()
    assert set(json.loads(stored)) and "key" not in stored.lower()
