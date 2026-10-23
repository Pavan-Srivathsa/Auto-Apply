"""The four boards that publish the posting themselves.

Jobright's Apply lands on the application, not the description: of the 134
distinct Ashby / Greenhouse / Lever / Workday URLs in `applications/`, 109
are a form URL. What was scraped off those pages was the form, or the
description with the form wrapped round it, or - on Workday, whose JSON-LD
block is the description with its own markup stripped - the whole posting on
one line. Each board answers with the description alone.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tailor import boards, fetch

DESCRIPTION = (
    "<p>We are hiring a backend engineer.</p><h3>Responsibilities</h3>"
    "<ul><li>Design services</li><li>Own reliability</li></ul>"
)


@pytest.mark.parametrize("url, system, org, job", [
    ("https://job-boards.greenhouse.io/embed/job_app?for=asm&token=4876722101&jr_id=x",
     "greenhouse", "asm", "4876722101"),
    ("https://job-boards.greenhouse.io/asm/jobs/4876722101", "greenhouse", "asm", "4876722101"),
    ("https://jobs.lever.co/hive/00c84379-62f1-45c9-9ec6-f882a1a7549b/apply?jr_id=x",
     "lever", "hive", "00c84379-62f1-45c9-9ec6-f882a1a7549b"),
    ("https://jobs.ashbyhq.com/plaid/7e10c0b5-a09a-4e07-aaa8-899a7f82a0c9/application",
     "ashby", "plaid", "7e10c0b5-a09a-4e07-aaa8-899a7f82a0c9"),
])
def test_the_form_url_names_the_same_job_as_the_posting(url, system, org, job):
    board = boards.identify(url)
    assert (board.system, board.org, board.job) == (system, org, job)


def test_workday_keeps_its_host_and_site_and_drops_the_apply_step():
    board = boards.identify(
        "https://kla.wd1.myworkdayjobs.com/en-US/annarbor/job/Ann-Arbor-MI/Engr--Software-1_2641480/apply")
    assert (board.system, board.org, board.site) == ("workday", "kla", "annarbor")
    assert board.job == "/job/Ann-Arbor-MI/Engr--Software-1_2641480"


def test_a_page_that_is_not_a_board_is_left_alone():
    assert boards.identify("https://careers.example.com/jobs/17") is None
    assert boards.identify("") is None


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_greenhouse_answers_the_description_without_the_form():
    def handler(request):
        assert request.url.path == "/v1/boards/asm/jobs/4876722101"
        return httpx.Response(200, json={"title": "Backend Engineer",
                                         "location": {"name": "Phoenix, AZ"},
                                         "content": DESCRIPTION})
    found = boards.posting(
        "https://job-boards.greenhouse.io/embed/job_app?for=asm&token=4876722101",
        _client(handler))
    assert "Own reliability" in found.text and "Accepted file types" not in found.text
    assert found.title == "Backend Engineer" and found.location == "Phoenix, AZ"
    assert found.authoritative and found.text_source == "greenhouse's job board"


def test_lever_keeps_its_named_lists_in_order():
    body = {"text": "Software Engineer - Frontend", "description": "<p>Intro.</p>",
            "categories": {"location": "Seattle"},
            "lists": [{"text": "Responsibilities", "content": "<li>Ship UI</li>"},
                      {"text": "Requirements", "content": "<li>React</li>"}],
            "additional": ""}
    found = boards.posting("https://jobs.lever.co/hive/00c84379-62f1-45c9-9ec6-f882a1a7549b/apply",
                           _client(lambda r: httpx.Response(200, json=body)))
    assert found.text.index("Responsibilities") < found.text.index("Requirements")
    assert "Ship UI" in found.text and "React" in found.text


def test_a_lever_posting_that_is_only_lists_still_reads():
    """Hive's posting has no description paragraph at all; the lists are it."""
    body = {"text": "Frontend", "description": "", "lists":
            [{"text": "Requirements", "content": "<li>TypeScript</li>"}]}
    found = boards.posting("https://jobs.lever.co/hive/00c84379-62f1-45c9-9ec6-f882a1a7549b",
                           _client(lambda r: httpx.Response(200, json=body)))
    assert "TypeScript" in found.text


def test_ashby_takes_the_one_job_out_of_the_board():
    job = "7e10c0b5-a09a-4e07-aaa8-899a7f82a0c9"
    body = {"jobs": [{"id": "other", "title": "Designer", "descriptionHtml": "<p>No.</p>"},
                     {"id": job, "title": "Backend", "location": "New York",
                      "descriptionHtml": DESCRIPTION}]}
    found = boards.posting(f"https://jobs.ashbyhq.com/plaid/{job}/application",
                           _client(lambda r: httpx.Response(200, json=body)))
    assert found.title == "Backend" and "Design services" in found.text
    assert "Designer" not in found.text


def test_a_closed_ashby_job_is_no_longer_on_the_board():
    found = boards.posting(
        "https://jobs.ashbyhq.com/uniswap/fb4d4137-f003-4669-beb7-2a5caca88012",
        _client(lambda r: httpx.Response(200, json={"jobs": []})))
    assert found is None


def test_workday_comes_back_with_its_line_breaks():
    """The JSON-LD block Workday leaves on the page is the same description
    with every tag taken out: 6,367 characters on one line."""
    body = {"jobPostingInfo": {"title": "Engr, Software 1", "location": "Ann Arbor, MI",
                               "jobDescription": DESCRIPTION}}
    def handler(request):
        assert request.url.path == "/wday/cxs/kla/annarbor/job/Ann-Arbor-MI/Engr--Software-1_2641480"
        return httpx.Response(200, json=body)
    found = boards.posting(
        "https://kla.wd1.myworkdayjobs.com/annarbor/job/Ann-Arbor-MI/Engr--Software-1_2641480",
        _client(handler))
    assert found.text.count("\n") >= 2
    assert "Responsibilities" in found.text


def test_a_closed_job_falls_back_to_the_ordinary_fetch():
    found = boards.posting("https://job-boards.greenhouse.io/embed/job_app?for=twitch&token=1",
                           _client(lambda r: httpx.Response(404, json={"error": "Job not found"})))
    assert found is None


def test_a_board_that_is_down_is_not_an_error():
    def handler(request):
        raise httpx.ConnectError("no route")
    assert boards.posting("https://jobs.lever.co/hive/00c84379-62f1-45c9-9ec6-f882a1a7549b",
                          _client(handler)) is None


def test_fetch_asks_the_board_before_it_reads_the_page(monkeypatch):
    posting = fetch.Posting(url="x", text="from the board", authoritative=True)
    monkeypatch.setattr(boards, "posting", lambda url, client=None: posting)
    def never(*args, **kwargs):
        raise AssertionError("the page was fetched anyway")
    monkeypatch.setattr(httpx, "Client", never)
    assert fetch.fetch("https://jobs.lever.co/hive/x").text == "from the board"


def test_a_gem_posting_is_read_from_its_api_not_its_javascript_page():
    import httpx as h
    from tailor import boards
    url = "https://jobs.gem.com/11x-ai/am9icG9zdDq8eyEoxWtqrxn_tFsQLKau"
    assert boards.identify(url).system == "gem"
    client = h.Client(transport=h.MockTransport(lambda r: h.Response(200, json={
        "title": "Software Engineer, Agents", "content_plain": "Build agents. " * 40, "location": {"name": "United States"}})))
    found = boards.posting(url, client)
    assert found.title == "Software Engineer, Agents" and found.authoritative and "agents" in found.text.lower()


def test_a_greenhouse_job_id_on_a_wrapper_page_and_a_workable_posting_are_read_from_their_apis():
    from tailor import boards
    puck = boards.identify("https://app.careerpuck.com/job-board/lyft/job/8896679002?gh_jid=8896679002")
    assert (puck.system, puck.org, puck.job) == ("greenhouse", "lyft", "8896679002")
    own = boards.identify("https://www.fivetran.com/careers/job?gh_jid=7811889003")
    assert (own.system, own.org, own.job) == ("greenhouse", "fivetran", "7811889003")
    wk = boards.identify("https://apply.workable.com/upsmith-1/j/7C745564D1/apply/")
    assert (wk.system, wk.org, wk.job) == ("workable", "upsmith-1", "7C745564D1")
