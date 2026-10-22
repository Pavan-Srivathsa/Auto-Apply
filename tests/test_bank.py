"""The bullet bank: drafts from the knowledge base, kept only when every
fact is on file and Jev rates them strong, reused instead of drafted again,
and never a change to the master resume. And the ATS keyword count."""
import json

import pytest

from tailor import bank, jev
from tailor import brief as briefs

MASTER = r"""\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading{AI Engineer}{BlackBuckInsights}{Bangalore}{2024}
\resumeItem{Built a Qwen analytics copilot answering 600--800 weekly queries}
\resumeSubheading{Data Scientist}{Koantek}{Bangalore}{2021 -- 2024}
\resumeItem{Developed SARIMAX forecasts on 300K+ records at 9.1\% MAPE}
\end{document}
"""
STORY = ("Summary: Data Scientist at Koantek.\nStack: PySpark, Databricks, XLNet\n"
         "Numbers: 0.89 Macro F1; 94% recall on high-intent leads; 10,000-15,000 leads per month")
BRIEF = briefs.Brief(role="ML Engineer", requirements=[
    briefs.Requirement(text="Feature pipelines in PySpark", kind="must", terms=["PySpark", "feature pipelines"],
                       evidence="Developed SARIMAX forecasts on 300K+ records at 9.1% MAPE"),
    briefs.Requirement(text="Classification models", kind="must", terms=["classification"], evidence="x"),
])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_BULLET_BANK", str(tmp_path / "bank.json"))
    stories = tmp_path / "stories"
    (stories / "data-scientist-koantek").mkdir(parents=True)
    (stories / "data-scientist-koantek" / "tailor.md").write_text(STORY)
    from tailor import profile
    monkeypatch.setattr(profile, "STORIES", stories)
    for name in (jev.API_KEY_ENV, "JEV_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_the_jobs_and_their_bullets_are_read_off_the_master():
    jobs = bank.jobs_on(MASTER)
    assert [(j["title"], j["company"]) for j in jobs] == [("AI Engineer", "BlackBuckInsights"), ("Data Scientist", "Koantek")]
    assert "9.1% MAPE" in jobs[1]["bullets"][0]
    assert bank.story_for("Koantek")[0] == "data-scientist-koantek" and bank.story_for("BlackBuckInsights") == ("", "")


def test_a_figure_or_a_name_that_is_not_on_file_keeps_a_draft_out():
    sources = STORY + "\nDeveloped SARIMAX forecasts on 300K+ records"
    assert bank.ungrounded("Classified 10,000-15,000 leads a month with XLNet at 0.89 Macro F1", sources) == []
    assert set(bank.ungrounded("Cut churn 35% with Snowflake", sources)) == {"35", "Snowflake"}


def drafts(texts):
    return lambda job, story, brief, model=None: [{"text": t, "terms": ["PySpark"], "covers": ["x"]} for t in texts]


def test_good_drafts_are_banked_and_reused_instead_of_drafted_again(monkeypatch):
    monkeypatch.setattr(bank, "ENOUGH", 2)
    good = ["Built PySpark pipelines classifying 10,000-15,000 leads per month with XLNet at 0.89 Macro F1",
            "Raised high-intent recall to 94% with an XLNet classification pipeline in PySpark on Databricks"]
    calls = []
    monkeypatch.setattr(bank, "draft", lambda *a, **k: calls.append(1) or drafts(good + ["Cut churn 35% with Snowflake", "Owned PySpark work"])(*a))
    monkeypatch.setattr(bank.jev, "configured", lambda: True)
    monkeypatch.setattr(bank.jev, "ask", lambda state, questions: {
        **{f"s{i}": {"noul": 0.9} for i in range(len(state["bullets"]))},
        **{f"q{i}": {"score": 1.9 if "Macro F1" in t or "94%" in t else 0.6} for i, t in enumerate(state["bullets"])}})
    first = bank.for_job(BRIEF, MASTER, job_key="job-1")
    koantek = next(j for j in first["jobs"] if j["company"] == "Koantek")
    assert [b["banked"] for b in koantek["drafted"]] == [True, True, False]          # the weak one is not banked
    assert any("35, Snowflake" in r["why"] for r in koantek["rejected"])             # the invented one never got judged
    assert [b.text for b in bank.load()] == good
    assert "Macro F1" in bank.options(first) and first["stories"] == ["data-scientist-koantek"]
    second = bank.for_job(BRIEF, MASTER, job_key="job-2")
    koantek = next(j for j in second["jobs"] if j["company"] == "Koantek")
    assert len(koantek["reused"]) == 2 and koantek["drafted"] == []                 # pulled from the bank
    # Run one drafted for both jobs; run two only for BlackBuckInsights, whose
    # drafts never made the bank. Koantek was pulled from it.
    assert len(calls) == 3
    assert all(b.uses == 1 and "job-2" in b.jobs for b in bank.load())


def test_without_a_judge_nothing_is_banked_and_a_bullet_can_be_deleted(monkeypatch):
    monkeypatch.setattr(bank, "draft", drafts(["Built PySpark pipelines classifying 10,000-15,000 leads per month"]))
    bank.for_job(BRIEF, MASTER)
    assert bank.load() == []
    bank.keep([bank.Bullet(company="Koantek", role="DS", text="Built PySpark pipelines")])
    [row] = bank.load()
    assert bank.delete(row.id) and bank.load() == []


def test_the_ats_count_reads_the_postings_terms_and_never_suggests_a_term_with_nothing_on_file():
    from server import review
    brief = briefs.Brief(requirements=[
        briefs.Requirement(text="Pipelines", kind="must", terms=["PySpark", "Airflow"], evidence="ran PySpark jobs"),
        briefs.Requirement(text="Go services", kind="must", terms=["Go"], evidence=""),
    ])
    ats = review._ats(brief, r"\resumeItem{Ran PySpark jobs over 2M rows}")
    assert (ats["present"], ats["total"]) == (1, 3)
    assert ats["missing_fixable"] == ["Airflow"] and ats["missing_no_evidence"] == ["Go"]


def test_the_resume_critic_asks_for_a_missing_term_only_where_evidence_exists():
    from tailor import critic
    brief = briefs.Brief(requirements=[
        briefs.Requirement(text="Pipelines", kind="must", terms=["PySpark"], evidence="ran PySpark jobs"),
        briefs.Requirement(text="Go services", kind="must", terms=["Go"], evidence=""),
    ])
    fixes = critic._coverage_fixes("resume", brief, "Built forecasting models")
    assert any('"PySpark"' in f for f in fixes) and not any('"Go"' in f for f in fixes)
