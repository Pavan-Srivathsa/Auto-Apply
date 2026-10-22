"""The critic loop: a rubric scored by Jev (or a text model without a key),
coverage counted in code, and a revision kept only when it scores higher
and still passes the writer's own rules."""
import json

import pytest

from tailor import brief as briefs
from tailor import critic, jev, llm

BRIEF = briefs.Brief(role="Data Engineer", requirements=[
    briefs.Requirement(text="Python data pipelines", kind="must", terms=["Airflow"],
                       evidence="Built an Airflow pipeline moving 2M rows a day", where="resume"),
    briefs.Requirement(text="Spark at scale", kind="must", terms=["Spark"], evidence=""),
])


@pytest.fixture(autouse=True)
def no_jev(monkeypatch):
    for name in (jev.API_KEY_ENV, "JEV_API_KEY", "JEV_BASE_URL", "JEV_MODEL"):
        monkeypatch.delenv(name, raising=False)


def jev_answers(levels, invented=0.1):
    def ask(state, questions, model=None, client=None):
        if "lines" in state:                         # the per-line invention check
            return {name: {"noul": invented if i == 0 else 0.05} for i, name in enumerate(questions)}
        assert set(questions) == {p.name for p in critic.EMAIL}
        assert state["brief"][1]["evidence"] is None             # a gap goes to Jev as null
        return {name: {"type": "score", "score": level} for name, level in levels.items()}
    return ask


def test_jev_scores_the_rubric_and_weak_points_become_fixed_instructions(monkeypatch):
    monkeypatch.setenv(jev.API_KEY_ENV, "key")
    monkeypatch.setattr(jev, "ask", jev_answers({"opening": 0.2, "proof": 2, "ask": 2, "voice": 1.6, "subject": 1.2}))
    c = critic.judge("email", "I moved 2M rows a day through Airflow.", BRIEF, subject="Data role")
    assert c.judge == "jev" and 1 <= c.score <= 10
    names = [p.name for p in critic.EMAIL if p.fix in c.fixes]
    assert names == ["opening", "subject"]                      # only the points below "strong"


def test_an_inventing_draft_is_capped_and_told_to_stop(monkeypatch):
    monkeypatch.setenv(jev.API_KEY_ENV, "key")
    monkeypatch.setattr(jev, "ask", jev_answers({p.name: 2 for p in critic.EMAIL}, invented=0.9))
    c = critic.judge("email", "Airflow, 2M rows a day, and I ran Spark on 10TB.", BRIEF,
                     sources={"resume": r"\resumeItem{Built an Airflow pipeline moving 2M rows a day}"})
    assert c.score == critic.INVENTED_CAP
    assert c.fixes[0].startswith("Cut or rewrite this line") and "Spark on 10TB" in c.fixes[0]


def test_a_line_whose_figures_and_names_are_on_file_is_never_capped(monkeypatch):
    monkeypatch.setenv(jev.API_KEY_ENV, "key")
    monkeypatch.setattr(jev, "ask", jev_answers({p.name: 2 for p in critic.EMAIL}, invented=0.9))
    c = critic.judge("email", "I built an Airflow pipeline moving 2M rows a day.", BRIEF,
                     sources={"resume": r"\resumeItem{Built an Airflow pipeline moving 2M rows a day}"})
    assert c.score > critic.INVENTED_CAP and c.invented <= critic.INVENTED


def test_an_unanswered_requirement_with_evidence_is_the_first_fix(monkeypatch):
    monkeypatch.setenv(jev.API_KEY_ENV, "key")
    monkeypatch.setattr(jev, "ask", jev_answers({p.name: 2 for p in critic.EMAIL}))
    c = critic.judge("email", "A note that never mentions the pipeline work.", BRIEF)
    assert c.fixes[0].startswith('Answer the required item "Python data pipelines"')
    assert not any("Spark" in fix for fix in c.fixes)            # a gap is never asked for


def test_without_a_key_the_text_model_scores_the_same_rubric(monkeypatch):
    seen = {}

    def complete(system, user, **kwargs):
        seen["user"] = user
        return "```json\n" + json.dumps({p.name: 2 for p in critic.COVER} | {"invented": False}) + "\n```"

    monkeypatch.setattr(llm, "complete", complete)
    c = critic.judge("cover", "Dear team, I built an Airflow pipeline moving 2M rows a day.", BRIEF)
    assert c.score == 10 and c.fixes == [] and c.judge
    assert "why_here" in seen["user"] and "Airflow" in seen["user"]


def scripted(scores):
    """A judge that returns these scores in order, with one fix while below 8."""
    it = iter(scores)

    def judge(kind, document, brief, subject="", sources=None):
        s = next(it)
        return critic.Critique(score=s, fixes=[] if s >= critic.THRESHOLD else ["make it better"], judge="test",
                               raw=s / 5)
    return judge


def test_refine_keeps_a_better_revision_and_stops_at_the_threshold(monkeypatch):
    monkeypatch.setattr(critic, "judge", scripted([5, 7, 9]))
    revisions = iter(["second", "third"])
    r = critic.refine("cover", "first", BRIEF, lambda text, fixes: next(revisions))
    assert (r.text, r.score, r.first_score, r.rounds, r.improved) == ("third", 9, 5, 2, True)


def test_refine_never_keeps_a_worse_or_rule_breaking_revision(monkeypatch):
    monkeypatch.setattr(critic, "judge", scripted([6, 4]))
    r = critic.refine("cover", "first", BRIEF, lambda text, fixes: "worse")
    assert r.text == "first" and r.score == 6 and r.trace[-1]["kept"] is False
    monkeypatch.setattr(critic, "judge", scripted([6, 9]))
    r = critic.refine("cover", "first", BRIEF, lambda text, fixes: "too long", check=lambda text: ["too long"])
    assert r.text == "first" and r.trace[-1]["rejected"] == "too long"


def test_refine_never_raises(monkeypatch):
    def down(*args, **kwargs):
        raise RuntimeError("judge down")
    monkeypatch.setattr(critic, "judge", down)
    r = critic.refine("email", "first", BRIEF, lambda text, fixes: "second")
    assert r.text == "first" and r.score == 0 and "no judge" in r.trace[0]["error"]
    monkeypatch.setattr(critic, "judge", scripted([5]))

    def broken(text, fixes):
        raise RuntimeError("writer down")
    r = critic.refine("email", "first", BRIEF, broken)
    assert r.text == "first" and "revision failed" in r.trace[-1]["error"]


def test_the_email_writer_revises_with_the_notes_and_keeps_its_rules(monkeypatch):
    from outreach.models import Contact
    from tailor import outreach as writer
    from tailor.answers import Context

    body = ("Hi Rita,\n\n" + "I built an Airflow pipeline moving 2M rows a day for an analytics team. " * 3
            + "\n\nWould you be the right person to ask about the Data Engineer role?\n\nBest,\nJane Doe")
    first = f"```subject\nRita, Data Engineer at Acme\n```\n```email\n{body}\n```"
    better = first.replace("Rita, Data Engineer at Acme", "Rita, the Data Engineer role at Acme")
    replies = iter([first, better])
    asked = []

    def complete(system, user, **kwargs):
        asked.append(user)
        return next(replies)

    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(critic, "judge", scripted([6, 9]))
    written = writer.write(Context(posting="Data Engineer", resume_tex="\\begin{document}x\\end{document}"),
                           Contact.create("recruiter", "Rita"), "Data Engineer", "Acme", "Jane Doe", brief=BRIEF)
    assert written.subject == "Rita, the Data Engineer role at Acme"
    assert written.review["score"] == 9 and written.review["first_score"] == 6
    assert "An editor's notes" in asked[1] and "make it better" in asked[1]
    assert "What this job asks for" in asked[1]                  # the brief goes with the revision


def test_the_resume_review_keeps_a_better_revision_with_its_diff_against_the_master(tmp_path, monkeypatch):
    import pipeline
    from tailor import tailor
    master = r"\begin{document}\resumeItem{Responsible for pipelines}\end{document}"
    first = tailor.TailorResult(tex=master.replace("Responsible for", "Ran"), suggestions="- first pass",
                                diff="", model="m", attempts=2, rejections=["attempt 1: x"])
    asked = {}

    def again(posting, **kw):
        asked.update(kw)
        return tailor.TailorResult(tex=master.replace("Responsible for pipelines", "Cut pipeline cost 30%"),
                                   suggestions="- revision", diff="vs tailored", model="m")

    monkeypatch.setattr(pipeline.tailor, "tailor", again)
    monkeypatch.setattr(pipeline, "make_page_check", lambda path: None)
    monkeypatch.setattr(pipeline, "base_page_count", lambda *a: 1)
    monkeypatch.setattr(critic, "judge", scripted([5, 8]))
    base = type("Base", (), {"tex_path": tmp_path / "r.tex", "pdf_path": tmp_path / "r.pdf"})()
    kept = pipeline.review_resume(tmp_path, object(), master, first, BRIEF, "", base, None)
    assert "Cut pipeline cost 30%" in kept.tex and asked["revision"] is True
    assert asked["resume_tex"] == first.tex                       # revised from the tailored resume, not the master
    assert "Responsible for pipelines" in kept.diff                # the diff is still against the master
    assert "Revised after review** (5 to 8/10)" in kept.suggestions and kept.attempts == 2
    assert json.loads((tmp_path / "resume_review.json").read_text())["score"] == 8


def test_a_resume_revision_that_fails_tailoring_leaves_the_first_one(tmp_path, monkeypatch):
    import pipeline
    from tailor import tailor
    first = tailor.TailorResult(tex="tailored", suggestions="s", diff="d", model="m")

    def refuses(posting, **kw):
        raise tailor.TailorError("gave up after 4 attempts; last problem: compiled to 2 pages")

    monkeypatch.setattr(pipeline.tailor, "tailor", refuses)
    monkeypatch.setattr(pipeline, "make_page_check", lambda path: None)
    monkeypatch.setattr(pipeline, "base_page_count", lambda *a: 1)
    monkeypatch.setattr(critic, "judge", scripted([5]))
    base = type("Base", (), {"tex_path": tmp_path / "r.tex", "pdf_path": tmp_path / "r.pdf"})()
    assert pipeline.review_resume(tmp_path, object(), "master", first, BRIEF, "", base, None) is first
    assert "revision failed" in json.loads((tmp_path / "resume_review.json").read_text())["trace"][-1]["error"]
