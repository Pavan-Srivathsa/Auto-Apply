"""Multi-resume registry and the cheap per-job router."""

import json

import pytest

from tailor import resume_bases
from tailor.fetch import Posting


@pytest.fixture
def registry(tmp_path, monkeypatch):
    root = tmp_path / "resumes"
    monkeypatch.setattr(resume_bases, "ROOT", root)
    monkeypatch.setattr(resume_bases, "REGISTRY", tmp_path / "resume_bases.json")
    monkeypatch.setattr(resume_bases, "CACHE", tmp_path / "resume_base_cache.json")
    monkeypatch.setattr(resume_bases, "LEGACY_TEX", tmp_path / "legacy.tex")
    monkeypatch.setattr(resume_bases, "LEGACY_PDF", tmp_path / "legacy.pdf")

    def write(rows, primary="swe"):
        values = []
        for base_id, label, roles, strengths in rows:
            directory = root / base_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "resume.tex").write_text(
                f"\\documentclass{{article}}\\begin{{document}}{label}\\end{{document}}"
            )
            (directory / "resume.pdf").write_bytes(b"%PDF")
            values.append({
                "id": base_id, "label": label, "description": "",
                "active": True, "summary": label,
                "target_roles": roles, "domains": [], "strengths": strengths,
            })
        resume_bases.REGISTRY.write_text(json.dumps({
            "primary": primary, "bases": values,
        }))

    return write


def posting(title="Software Engineer", text="Build reliable backend APIs with Python."):
    return Posting(url="https://example.com/jobs/1", title=title, text=text,
                   company="Example")


def test_exact_role_rule_selects_one_of_three_without_a_model(registry, monkeypatch):
    registry([
        ("swe", "Software", ["Software Engineer"], ["Python", "APIs"]),
        ("ml", "Machine Learning", ["Machine Learning Engineer"], ["PyTorch", "MLflow"]),
        ("data", "Data Science", ["Data Scientist"], ["Forecasting", "Statistics"]),
    ])
    monkeypatch.setattr(
        resume_bases.llm, "complete",
        lambda *a, **k: pytest.fail("an exact role match must not call a model"),
    )

    choice = resume_bases.choose(posting("Machine Learning Engineer"))

    assert choice.base == "ml"
    assert choice.source == "rule"
    assert choice.confidence == "high"


def test_model_choice_is_cached_and_profile_changes_invalidate_it(registry, monkeypatch):
    registry([
        ("swe", "Software", [], ["Python", "APIs"]),
        ("ml", "Machine Learning", [], ["PyTorch", "MLflow"]),
        ("data", "Data Science", [], ["Forecasting", "Statistics"]),
    ])
    calls = []

    def complete(system, user, **kwargs):
        calls.append(user)
        return '{"base":"swe","confidence":"medium","reason":"Backend API evidence."}'

    monkeypatch.setattr(resume_bases.llm, "complete", complete)
    first = resume_bases.choose(posting())
    second = resume_bases.choose(posting())
    (resume_bases.ROOT / "swe" / "resume.tex").write_text("changed")
    third = resume_bases.choose(posting())

    assert first.source == "model"
    assert second.source == "cache" and second.base == "swe"
    assert third.source == "model"
    assert len(calls) == 2
    assert all('"ml"' in call and '"data"' in call for call in calls)


def test_router_failure_uses_the_primary_resume(registry, monkeypatch):
    registry([
        ("swe", "Software", [], ["Python"]),
        ("ml", "Machine Learning", [], ["PyTorch"]),
        ("data", "Data Science", [], ["Statistics"]),
    ], primary="data")
    monkeypatch.setattr(
        resume_bases.llm, "complete",
        lambda *a, **k: (_ for _ in ()).throw(resume_bases.llm.LLMError("down")),
    )

    choice = resume_bases.choose(posting("Unusual Technical Role", "Unrelated wording"))

    assert choice.base == "data"
    assert choice.source == "fallback"
    assert choice.confidence == "low"


def test_manual_choice_beats_rules_and_the_model(registry, monkeypatch):
    registry([
        ("swe", "Software", ["Software Engineer"], ["Python"]),
        ("ml", "Machine Learning", ["Machine Learning Engineer"], ["PyTorch"]),
        ("data", "Data Science", ["Data Scientist"], ["Statistics"]),
    ])
    monkeypatch.setattr(
        resume_bases.llm, "complete",
        lambda *a, **k: pytest.fail("manual selection must not call a model"),
    )

    choice = resume_bases.choose(posting("Machine Learning Engineer"), requested="data")

    assert choice.base == "data"
    assert choice.source == "manual"
