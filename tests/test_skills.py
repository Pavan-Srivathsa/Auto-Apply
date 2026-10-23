"""The skills index: aliases find a skill, a role counts once, gaps follow the
verdicts, soft skills stay out of the gap list, and bundles need lift."""
import pytest

from career import skills


def test_aliases_find_one_skill_each():
    assert set(skills.skills_in("Experience with k8s, golang and PyTorch")) >= {"Kubernetes", "Go", "PyTorch"}
    assert "Go" not in skills.skills_in("You will go above and beyond")
    assert "C++" in skills.skills_in("Strong C++ and CUDA") and "CUDA" in skills.skills_in("Strong C++ and CUDA")
    assert "C" not in skills.skills_in("Strong C++") and "C" in skills.skills_in("Embedded C, Rust")
    assert "Go" in skills.skills_in("Python or Go")


def test_counts_are_per_role_and_gaps_follow_the_verdicts(monkeypatch):
    roles = [
        {"title": "ML Engineer", "company": "A", "family": "ml", "source": "scout", "items": [
            {"text": "Python and PyTorch", "kind": "must", "level": "meets"},
            {"text": "More Python, TypeScript", "kind": "must", "level": "missing"},
            {"text": "Great communication", "kind": "must", "level": "missing"}]},
        {"title": "ML Engineer II", "company": "B", "family": "ml", "source": "scout", "items": [
            {"text": "Python, PyTorch, TypeScript", "kind": "must", "level": "partly"},
            {"text": "Communication with stakeholders", "kind": "nice", "level": "missing"}]},
        {"title": "ML Engineer III", "company": "C", "family": "ml", "source": "scout", "items": [
            {"text": "Python; PyTorch; TypeScript", "kind": "must", "level": "missing"}]},
    ]
    monkeypatch.setattr(skills, "_roles", lambda: roles)
    out = skills.build(save=False)
    by = {s["skill"]: s for s in out["skills"]}
    assert by["Python"]["roles"] == 3                                   # once per role, not per line
    assert (by["Python"]["have"], by["Python"]["partly"], by["Python"]["missing"]) == (1, 1, 1)   # best line per role
    assert by["TypeScript"]["missing"] == 2
    assert "Communication" not in {s["skill"] for s in out["missing_most"]}
    # Two skills in every role of the family are not a bundle: lift is 1.
    assert not any(set(x["skills"]) == {"PyTorch", "Python"} for x in out["by_family"]["ml"]["together"])
