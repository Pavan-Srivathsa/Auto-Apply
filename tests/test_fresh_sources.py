"""The week rule, Simplify's list and Jobright's lists."""
from datetime import date, datetime, timezone

from scout import filter as level, jobright, simplify


def test_every_way_a_board_writes_a_date_reads_as_an_age():
    today = date(2026, 10, 23)
    assert level.age_days("2026-10-22", today) == 1
    assert level.age_days("Posted Yesterday", today) == 1 and level.age_days("Posted Today", today) == 0
    assert level.age_days("Posted 8 Days Ago", today) == 8 and level.age_days("Posted 30+ Days Ago", today) == 30
    assert level.age_days("", today) is None and level.age_days("soon", today) is None
    assert level.too_old("2026-10-10", today) and not level.too_old("2026-10-17", today) and not level.too_old("", today)


def test_simplify_keeps_active_recent_software_rows_only():
    now = datetime(2026, 10, 23, tzinfo=timezone.utc).timestamp()
    day = 86400
    rows = [
        {"id": "a", "title": "SWE New Grad", "company_name": "Acme", "url": "https://x/a", "active": "True", "is_visible": "True",
         "date_posted": str(now - 2 * day), "category": "Software", "locations": "['NYC', 'SF']"},
        {"id": "b", "title": "Old", "company_name": "Acme", "url": "https://x/b", "active": "True", "is_visible": "True",
         "date_posted": str(now - 10 * day), "category": "Software", "locations": "[]"},
        {"id": "c", "title": "Closed", "company_name": "Acme", "url": "https://x/c", "active": "False", "is_visible": "True",
         "date_posted": str(now - day), "category": "Software", "locations": "[]"},
        {"id": "d", "title": "Chip designer", "company_name": "Acme", "url": "https://x/d", "active": "True", "is_visible": "True",
         "date_posted": str(now - day), "category": "Hardware", "locations": "[]"},
    ]
    [row] = simplify.parse(rows, simplify.CATEGORIES, 7, now)
    assert row.id == "a" and row.location == "NYC / SF" and row.posted == "2026-10-21"


def test_jobright_rows_carry_only_the_list_never_jobrights_match():
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    cards = [{"id": "1", "url": "https://jobright.ai/jobs/info/1", "title": "AI Engineer", "company": "Acme",
              "age": "2 days ago", "place": "SF", "lists": ["Recommended", "Top Matched"], "match": 91},
             {"id": "2", "url": "https://jobright.ai/jobs/info/2", "title": "Old", "company": "Acme",
              "age": "2 weeks ago", "place": "SF", "lists": ["Recommended"]}]
    [row] = jobright.to_postings(cards, 7, now)
    assert row.posted == "2026-10-21" and row.note == "Jobright Recommended + Top Matched" and "91" not in row.note
    assert jobright.age_to_date("20 minutes ago", now) == "2026-10-23" and jobright.age_to_date("yesterday", now) == "2026-10-22"
