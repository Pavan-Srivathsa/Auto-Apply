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


def test_a_posting_that_will_not_sponsor_is_out_before_anything_else():
    from scout import filter as level
    for text in ["US citizen/visa only",
                 "We are unable to sponsor visas for this role.",
                 "Candidates must be authorized to work in the US without current or future sponsorship.",
                 "This company does not offer visa sponsorship.",
                 "Open to U.S. citizens or permanent residents only.",
                 "We will not sponsor H-1B.",
                 "No visa sponsorship is available. 6+ years required."]:
        assert level.prescreen(text) == level.NO_SPONSOR, text
    for text in ["Will you now or in the future require sponsorship?", "We sponsor visas.",
                 "Visa sponsorship available for the right candidate.", "US citizenship/visa not required"]:
        assert level.prescreen(text) != level.NO_SPONSOR, text


def test_yc_never_lists_a_citizen_or_visa_only_role():
    import html as h, json
    from scout import yc
    jobs = [{"id": 1, "title": "SWE", "url": "/companies/a/jobs/1", "visa": "US citizen/visa only"},
            {"id": 2, "title": "SWE", "url": "/companies/b/jobs/2", "visa": "Will sponsor"}]
    page = f'<div data-page="{h.escape(json.dumps({"props": {"jobPostings": jobs}}))}"></div>'
    assert [p.id for p in yc.parse(page)] == ["2"]


def test_a_forms_question_or_answer_about_sponsorship_is_not_a_refusal():
    from scout import filter as level
    for text in ["Are you legally authorized to work in the U.S. without sponsorship?\nYes\nNo",
                 "Yes, I am authorized to work in the U.S. without sponsorship",
                 "No, I do not require visa sponsorship to work in the United States"]:
        assert not level.needs_no_sponsor(text), text
    assert level.needs_no_sponsor("About the role. Candidates must be authorized to work in the U.S. without sponsorship.")
    assert level.needs_no_sponsor("Do you need a visa?\nWe are unable to sponsor visas for this role.")
