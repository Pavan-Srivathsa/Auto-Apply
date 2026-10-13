"""The line budget: what a printed line holds, and how many a bullet takes."""

import pytest

from tailor import layout

BULLET = (r"\normalsize{\href{https://github.com/me/hydra}{\textbf{\color{blue}"
          r"\underline{Project Hydra:}}} Built a fault-tolerant platform}")


def test_visible_text_drops_macros_and_does_not_count_the_url():
    seen = layout.visible(BULLET)
    assert seen == "Project Hydra: Built a fault-tolerant platform"
    assert "github.com" not in seen


def test_a_line_is_measured_off_the_compiled_master(tmp_path, monkeypatch):
    """The width is read from the PDF's own wrapped lines, cached by the
    file's modification time, and falls back to a constant without one."""
    monkeypatch.setattr(layout, "CACHE", tmp_path / "line_width.json")
    monkeypatch.setattr(layout, "_measure", lambda path: 98)
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.5 fake")
    assert layout.chars_per_line(pdf) == 98
    # Second call reads the cache rather than measuring again.
    monkeypatch.setattr(layout, "_measure", lambda path: 1)
    assert layout.chars_per_line(pdf) == 98
    assert layout.chars_per_line(tmp_path / "missing.pdf") == layout.DEFAULT_CHARS_PER_LINE


def test_line_width_cache_is_separate_for_each_resume_format(tmp_path, monkeypatch):
    monkeypatch.setattr(layout, "CACHE", tmp_path / "line_width.json")
    first = tmp_path / "compact.pdf"
    second = tmp_path / "wide.pdf"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    widths = {first: 82, second: 117}
    monkeypatch.setattr(layout, "_measure", lambda path: widths[path])

    assert layout.chars_per_line(first) == 82
    assert layout.chars_per_line(second) == 117
    monkeypatch.setattr(layout, "_measure", lambda path: pytest.fail("must read each cached width"))
    assert layout.chars_per_line(first) == 82
    assert layout.chars_per_line(second) == 117


def test_the_budget_is_lines_and_the_characters_that_fit(monkeypatch):
    monkeypatch.setattr(layout, "chars_per_line", lambda pdf_path=None: 40)
    short = r"\normalsize{Cut p95 latency 52%}"
    two = r"\normalsize{" + "x" * 70 + "}"
    assert layout.line_count(short, 40) == 1
    lines, cap, used = layout.budget(two, 40)
    assert (lines, cap, used) == (2, 86, 70)


def test_an_environment_override_wins(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_CHARS_PER_LINE", "77")
    assert layout.chars_per_line() == 77
