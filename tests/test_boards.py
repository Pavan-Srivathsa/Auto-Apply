"""a16z's portfolio board and YC's job board as Scout sources: cards and
page data turned into postings, and what each source does with a role."""
import html
import json

from scout import Watch, a16z, detect, filter as level, yc

CARD = ('<div><span>{company}</span><h3>{title}</h3><span>{salary}</span><span>·</span><span>{place}</span>'
        '<span>·</span><span>Posted {age} ago</span><span>Fintech</span><span>·</span><span>IPO</span></div>'
        '<a href="{url}" target="_blank" rel="noopener noreferrer" class="relative z-10 mt-2.5 inline-flex">Apply</a>')


def test_a16z_cards_become_postings_with_the_employers_own_link():
    page = "<header>All jobs</header>" + "".join([
        CARD.format(company="Stripe", title="Software Engineer", salary="USD 150,000–200,000", place="Seattle, WA",
                    age="an hour", url="https://stripe.com/jobs/search?gh_jid=1&amp;utm_source=a16z"),
        CARD.format(company="Asana", title="Software Engineer", salary="", place="San Francisco", age="8 days",
                    url="https://www.asana.com/jobs/apply/2?gh_jid=2"),
    ])
    first, second = a16z.parse(page)
    assert (first.company, first.title, first.location) == ("Stripe", "Software Engineer", "Seattle, WA")
    assert first.url == "https://stripe.com/jobs/search?gh_jid=1" and first.posted       # tracking tag off
    assert (second.company, second.location) == ("Asana", "San Francisco")


def test_yc_page_data_becomes_postings_and_senior_roles_are_left_out():
    jobs = [{"id": 1, "title": "Product Engineer", "url": "/companies/a/jobs/1", "location": "New York, NY, US",
             "companyName": "Alpha", "minExperience": "1+ years", "createdAt": "10 days"},
            {"id": 2, "title": "Senior Engineer", "url": "/companies/b/jobs/2", "location": "SF",
             "companyName": "Beta", "minExperience": "6+ years", "createdAt": "2 days"}]
    page = f'<div data-page="{html.escape(json.dumps({"props": {"jobPostings": jobs}}))}"></div>'
    [row] = yc.parse(page)
    assert (row.company, row.url, row.text) == ("Alpha", "https://www.ycombinator.com/companies/a/jobs/1", "")
    assert row.posted and len(row.posted) == 10


def test_detect_names_both_and_yc_lists_only():
    assert detect("https://jobs.a16z.com/jobs")[0] == "a16z"
    provider, args, _ = detect("https://www.ycombinator.com/jobs/role/software-engineer")
    assert provider == "yc" and args["hold"] is True         # applying is behind the person's YC login
