"""The story bank reads the interview's files, never rewrites them, checks
every figure against the candidate's own words, and keeps a story a draft
until the person confirms it."""
import json

import pytest

from career import stories


@pytest.fixture
def bank(tmp_path, monkeypatch):
    from tailor import profile
    root = tmp_path / "stories"
    (root / "ml-engineer-acme").mkdir(parents=True)
    (root / "ml-engineer-acme" / "main.md").write_text("I cut p95 latency 28% on the serving path at Acme.")
    (root / "ml-engineer-acme" / "star.md").write_text(
        "# Star Document — Serving latency (Acme)\n## Situation\nThe inference service at Acme was slow under load "
        "for every analyst query.\n## Task\nBring p95 latency down without new hardware for the team.\n"
        "## Action\nI added request queuing and Redis caching, and benchmarked vLLM against the old server.\n"
        "## Result\np95 latency fell 28% and errors fell 90% in production.\n### Reflection\nMeasure first.\n")
    (root / "ml-engineer-acme" / "tailor.md").write_text("Stack: vLLM, Redis, FastAPI\n")
    (root / "empty-one").mkdir()
    (root / "empty-one" / "star.md").write_text("# Story\n### Situation\nNot discussed.\n### Result\nNot discussed.\n")
    monkeypatch.setattr(profile, "STORIES", root)
    monkeypatch.setenv("AUTOPILOT_STORY_STATE", str(tmp_path / "state.json"))
    return root


def test_a_story_is_read_with_its_provenance_and_stays_a_draft(bank):
    by = {s.slug: s for s in stories.bank()}
    acme = by["ml-engineer-acme"]
    assert acme.title == "Serving latency (Acme)" and acme.lessons == "Measure first."
    assert "28" in acme.figures_confirmed and "90" in acme.figures_unconfirmed      # 90% is in no own words
    assert {"system_optimization", "model_deployment"} <= set(acme.categories) and acme.status == "draft"
    assert acme.technologies == ["vLLM", "Redis", "FastAPI"]
    assert by["empty-one"].completeness == 0 and not by["empty-one"].situation       # "Not discussed" is empty


def test_confirming_a_story_never_touches_the_interviews_files(bank):
    before = (bank / "ml-engineer-acme" / "star.md").read_text()
    stories.confirm("ml-engineer-acme")
    assert next(s for s in stories.bank() if s.slug == "ml-engineer-acme").status == "verified"
    assert (bank / "ml-engineer-acme" / "star.md").read_text() == before
