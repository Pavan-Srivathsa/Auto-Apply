"""Posting extraction from HTML."""

import json

import pytest

from tailor import fetch

DESCRIPTION = (
    "We are hiring a backend engineer to work on distributed systems. "
    "You will design services, own reliability, and mentor others. " * 6
)


def page(body: str, ld: dict | None = None) -> str:
    script = (
        f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld else ""
    )
    return f"<html><head><title>Backend Engineer at Example</title>{script}</head><body>{body}</body></html>"


def test_flattens_html_to_text():
    html = page(f"<div><p>{DESCRIPTION}</p><script>var x=1;</script></div>")
    posting = _fetch_html(html)
    assert "distributed systems" in posting.text
    assert "var x=1" not in posting.text


def test_prefers_json_ld_metadata():
    ld = {
        "@type": "JobPosting",
        "title": "Senior Backend Engineer",
        "hiringOrganization": {"name": "Example Corp"},
        "jobLocation": {"address": {"addressLocality": "Berlin", "addressCountry": "DE"}},
        "description": f"<p>{DESCRIPTION}</p>",
    }
    posting = _fetch_html(page("<p>short page body</p>", ld))
    assert posting.title == "Senior Backend Engineer"
    assert posting.company == "Example Corp"
    assert "Berlin" in posting.location
    assert "distributed systems" in posting.text


def test_falls_back_to_title_tag():
    posting = _fetch_html(page(f"<p>{DESCRIPTION}</p>"))
    assert posting.title == "Backend Engineer at Example"


def test_thin_page_raises_javascript_required():
    with pytest.raises(fetch.JavaScriptRequired):
        _fetch_html(page("<div id='root'></div>"))


def test_markdown_carries_the_header_fields():
    ld = {
        "@type": "JobPosting",
        "title": "Backend Engineer",
        "hiringOrganization": {"name": "Example Corp"},
        "description": f"<p>{DESCRIPTION}</p>",
    }
    markdown = _fetch_html(page("<p>x</p>", ld)).to_markdown()
    assert markdown.startswith("# Backend Engineer")
    assert "**Company:** Example Corp" in markdown
    assert "https://example.com/jobs/1" in markdown


def _fetch_html(html: str, url: str = "https://example.com/jobs/1"):
    """Run fetch.fetch against canned HTML instead of the network."""
    import httpx

    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    real_client = httpx.Client

    class PatchedClient(real_client):
        def __init__(self, **kwargs):
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    httpx.Client = PatchedClient
    try:
        return fetch.fetch(url)
    finally:
        httpx.Client = real_client


def test_a_greenhouse_link_reads_the_description_from_the_board_api():
    """The embedded form shows only the application questions; Stripe's
    posting came through as its form and every document was tailored blind."""
    from tailor import fetch as fetcher
    assert fetcher.greenhouse_job("https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8224915") == ("stripe", "8224915")
    assert fetcher.greenhouse_job("https://boards.greenhouse.io/acme/jobs/42?gh_jid=42") == ("acme", "42")
    assert fetcher.greenhouse_job("https://jobs.ashbyhq.com/acme/1") is None
    body = "&lt;p&gt;Growth Platform builds the machine learning systems " + "that help businesses grow. " * 30 + "&lt;/p&gt;"

    class Client:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            data = {"title": "Machine Learning Engineer, Growth Platform", "content": body,
                    "location": {"name": "San Francisco"}}
            return type("R", (), {"status_code": 200, "json": lambda self: data})()

    client = Client()
    posting = fetcher._greenhouse("https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8224915", client)
    assert client.urls == ["https://boards-api.greenhouse.io/v1/boards/stripe/jobs/8224915"]
    assert posting.title == "Machine Learning Engineer, Growth Platform" and posting.location == "San Francisco"
    assert posting.text.startswith("Growth Platform builds") and "<p>" not in posting.text
