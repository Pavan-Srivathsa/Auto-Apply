"""The job brief: requirements read from the posting, evidence that must be
real, Jev's judgement when a key is set, and coverage counted in code."""
import json

import pytest

from tailor import brief as briefs
from tailor import jev

RESUME = r"""\begin{document}
\resumeItem{Built a Python ingestion pipeline on Airflow moving 2M rows a day into PostgreSQL}
\resumeItem{Led a React dashboard rewrite used by 40 analysts}
\end{document}"""
PROFILE = "Stack: Go, Kubernetes. Shipped a Go service on Kubernetes for a class project."


def reply(requirements):
    return "```json\n" + json.dumps({"role": "Data Engineer", "requirements": requirements}) + "\n```"


@pytest.fixture(autouse=True)
def no_jev(monkeypatch):
    for name in (jev.API_KEY_ENV, "JEV_API_KEY", "JEV_BASE_URL", "JEV_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_real_quotes_are_kept_and_invented_ones_become_gaps():
    brief = briefs.parse(reply([
        {"text": "Build data pipelines in Python", "kind": "must", "terms": ["Python", "Airflow"],
         "evidence": "Built a Python ingestion pipeline on Airflow moving 2M rows a day into PostgreSQL"},
        {"text": "Kubernetes in production", "kind": "nice", "terms": ["Kubernetes"],
         "evidence": "Shipped a Go service on Kubernetes"},
        {"text": "Spark at scale", "kind": "must", "terms": ["Spark"],
         "evidence": "Ran Spark jobs over 10TB nightly"},          # not on the resume
    ]), RESUME, PROFILE)
    first, spark, k8s = brief.requirements                         # musts first, model order kept
    assert first.where == "resume" and not first.gap
    assert spark.text == "Spark at scale" and spark.gap
    assert k8s.where == "profile" and k8s.kind == "nice"


def test_the_message_tells_the_writer_not_to_claim_a_gap():
    brief = briefs.parse(reply([{"text": "Spark at scale", "kind": "must", "terms": ["Spark"], "evidence": ""}]),
                         RESUME, PROFILE)
    text = brief.as_message()
    assert "Spark at scale" in text and "Do not claim it" in text


def test_coverage_counts_only_required_items_with_evidence():
    brief = briefs.parse(reply([
        {"text": "Python pipelines", "kind": "must", "terms": ["Airflow"],
         "evidence": "Built a Python ingestion pipeline on Airflow moving 2M rows a day into PostgreSQL"},
        {"text": "Dashboards", "kind": "must", "terms": ["React"],
         "evidence": "Led a React dashboard rewrite used by 40 analysts"},
        {"text": "Spark", "kind": "must", "terms": ["Spark"], "evidence": ""},
    ]), RESUME, PROFILE)
    cov = briefs.coverage(brief, "I moved 2M rows a day through Airflow.")
    assert cov["musts_answerable"] == 2 and cov["musts_covered"] == 1
    assert [r["covered"] for r in cov["rows"]] == [True, False, False]


def test_a_bad_reply_raises():
    with pytest.raises(briefs.BriefError):
        briefs.parse("no json here", RESUME, PROFILE)
    with pytest.raises(briefs.BriefError):
        briefs.parse(reply([]), RESUME, PROFILE)


def test_save_and_load_round_trip(tmp_path):
    brief = briefs.parse(reply([{"text": "Python pipelines", "kind": "must", "terms": ["Python"],
                                 "evidence": "Led a React dashboard rewrite used by 40 analysts"}]), RESUME, PROFILE)
    briefs.save(tmp_path, brief)
    assert briefs.load(tmp_path).to_dict() == brief.to_dict()
    assert briefs.load(tmp_path / "missing") is None


def test_jev_turns_a_stretch_into_a_gap(monkeypatch):
    monkeypatch.setenv(jev.API_KEY_ENV, "key")
    asked = {}

    def ask(state, questions, model=None, client=None):
        asked.update(state=state, questions=questions)
        return {"r0": {"type": "noul", "noul": 0.93}, "r1": {"type": "noul", "noul": 0.2}}

    monkeypatch.setattr(jev, "ask", ask)
    brief = briefs.judge(briefs.parse(reply([
        {"text": "Python pipelines", "kind": "must", "terms": ["Python"],
         "evidence": "Built a Python ingestion pipeline on Airflow moving 2M rows a day into PostgreSQL"},
        {"text": "Production Kubernetes operations", "kind": "must", "terms": ["Kubernetes"],
         "evidence": "Shipped a Go service on Kubernetes"},
    ]), RESUME, PROFILE))
    real, stretch = brief.requirements
    assert real.strength == 0.93 and not real.gap
    assert stretch.strength == 0.2 and stretch.gap                 # a class project is not production ops
    assert len(asked["state"]["requirements"]) == 2 and asked["questions"]["r0"]["type"] == "noul"


def test_without_a_key_or_when_jev_fails_the_brief_is_unchanged(monkeypatch):
    brief = briefs.parse(reply([{"text": "Python pipelines", "kind": "must", "terms": ["Python"],
                                 "evidence": "Led a React dashboard rewrite used by 40 analysts"}]), RESUME, PROFILE)
    assert briefs.judge(brief).requirements[0].strength is None
    monkeypatch.setenv(jev.API_KEY_ENV, "key")

    def down(*args, **kwargs):
        raise jev.JevError("Jev answered 503")

    monkeypatch.setattr(jev, "ask", down)
    assert not briefs.judge(brief).requirements[0].gap


class Http:
    def __init__(self, status=200, data=None):
        self.status, self.data, self.sent = status, data, {}

    def post(self, url, headers, json):
        self.sent = {"url": url, "headers": headers, "json": json}
        status, data = self.status, self.data

        class Response:
            status_code = status
            text = str(data)

            def json(self_inner):
                return data
        return Response()


def test_jev_client_sends_the_documented_request(monkeypatch):
    monkeypatch.setenv(jev.API_KEY_ENV, "secret")
    http = Http(data={"answers": {"fit": {"type": "noul", "noul": 0.7}}})
    answers = jev.ask({"text": "x"}, {"fit": jev.noul("Is it a fit?")}, client=http)
    assert answers["fit"]["noul"] == 0.7
    assert http.sent["url"] == "https://api.typesafe.ai/v1/systemone"
    assert http.sent["headers"]["Authorization"] == "Bearer secret"
    assert http.sent["json"]["model"] == "jev-latest"
    assert "TypeSafe-Danger-Zone-Long-Context" not in http.sent["headers"]
    jev.ask({"text": "x" * (jev.LONG_STATE + 1)}, {"fit": jev.noul("?")}, client=http)
    assert http.sent["headers"]["TypeSafe-Danger-Zone-Long-Context"] == "true"


def test_jev_client_errors_are_jev_errors(monkeypatch):
    with pytest.raises(jev.JevError):
        jev.ask({}, {"q": jev.noul("?")})                          # no key
    monkeypatch.setenv(jev.API_KEY_ENV, "secret")
    with pytest.raises(jev.JevError):
        jev.ask({}, {"q": jev.noul("?")}, client=Http(status=401, data={"detail": "bad key"}))
    with pytest.raises(jev.JevError):
        jev.ask({}, {"q": jev.noul("?")}, client=Http(data={"answers": {}}))


def test_the_jev_names_in_the_persons_env_are_read(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", '"secret"')
    monkeypatch.setenv("JEV_MODEL", "jev-1.14")
    http = Http(data={"answers": {"q": {"type": "noul", "noul": 0.5}}})
    assert jev.configured()
    jev.ask({}, {"q": jev.noul("?")}, client=http)
    assert http.sent["headers"]["Authorization"] == "Bearer secret" and http.sent["json"]["model"] == "jev-1.14"
