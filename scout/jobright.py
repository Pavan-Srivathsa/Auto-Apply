"""Jobright's own lists, Recommended and Top Fit, as a Scout source.

Both are behind the person's Jobright login, so they are read in Autopilot's
Chrome (where that login lives), the way Lenny's is: the recommend page,
sorted by each of `args.sorts` in turn through the page's own sort menu,
scrolled until `MAX_CARDS` or nothing new. Each card gives its age ("2 days
ago"), title, company and place; Jobright's own match and labels are not
read. The link is Jobright's job
page: Jobright does not expose the employer's link, its Apply opens it, so a
row is opened on Jobright and the banner does the rest (screen, Apply,
queue); `/capture` still refuses jobright.ai URLs. A watch on it holds.

Ages older than `args.days` (7) are dropped here: "If it's older than a week,
there's no point applying" (2026-10-23).
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

from scout import Posting, Watch

URL = "https://jobright.ai/jobs/recommend"
SORTS = ["Recommended", "Top Matched"]           # Jobright's label for what the person calls Top Fit
ALIASES = {"top fit": "Top Matched", "most recent": "Most Recent"}
MAX_CARDS = 60
DAYS = 7

CARDS_JS = r"""(() => {
  const seen = new Map();
  for (const a of document.querySelectorAll('a[href*="/jobs/info/"]')) {
    const id = (a.href.match(/\/jobs\/info\/([0-9a-f]+)/) || [])[1];
    if (!id || seen.has(id)) continue;
    let card = a;
    for (let i = 0; i < 10 && card; i++) { card = card.parentElement; if (card && /APPLY WITH AUTOFILL|APPLY NOW/i.test(card.innerText)) break; }
    if (!card) continue;
    const lines = card.innerText.split('\n').map((s) => s.trim()).filter(Boolean);
    const h2 = card.querySelector('h2');
    const title = h2 ? h2.innerText.trim() : '';
    const company = (h2 && h2.nextElementSibling ? h2.nextElementSibling.innerText : (lines[lines.indexOf(title) + 1] || ''))
                  .split('\n')[0].replace(/\s*\/\s*$/, '').trim();
    const age = lines.find((l) => /(ago|just now|today|yesterday)$/i.test(l)) || '';
    const slash = lines.indexOf('/');
    const place = slash > 0 ? (lines[slash + 2] || '') : '';
    seen.set(id, {id, url: 'https://jobright.ai/jobs/info/' + id, title, company, age, place});
  }
  return [...seen.values()];
})()"""

SORT_OPEN_JS = r"""(() => {
  const e = [...document.querySelectorAll('.ant-select')].find((x) => /recommended|top fit|most recent|newest/i.test(x.innerText));
  if (!e) return null;
  e.scrollIntoView({block: 'center', behavior: 'instant'});
  const r = e.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2, now: e.innerText.trim()};
})()"""

SORT_PICK_JS = r"""((want) => {
  const opt = [...document.querySelectorAll('.ant-select-item-option, [role=option]')].find((x) => x.innerText.trim().toLowerCase() === want.toLowerCase());
  if (!opt) return [...document.querySelectorAll('.ant-select-item-option, [role=option]')].map((x) => x.innerText.trim());
  opt.scrollIntoView({block: 'center', behavior: 'instant'});
  const r = opt.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2};
})"""


def age_to_date(age: str, now: datetime | None = None) -> str:
    """"2 days ago" / "20 minutes ago" / "yesterday" as an ISO date; "" when unread."""
    now = now or datetime.now(timezone.utc)
    low = (age or "").lower().strip()
    if not low:
        return ""
    if "just now" in low or "today" in low or re.search(r"\b(second|minute|hour)s?\b", low):
        return now.strftime("%Y-%m-%d")
    if "yesterday" in low:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    found = re.search(r"(\d+|an?|one)\s+(day|week|month|year)s?", low)
    if not found:
        return ""
    n = 1 if found.group(1) in ("a", "an", "one") else int(found.group(1))
    days = n * {"day": 1, "week": 7, "month": 30, "year": 365}[found.group(2)]
    return (now - timedelta(days=days)).strftime("%Y-%m-%d")


async def _click(page, spot) -> None:
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        await page.send("Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"], "button": "left", "clickCount": 1})


async def _sort(page, want: str) -> None:
    want = ALIASES.get(want.lower(), want)
    spot = await page.evaluate(SORT_OPEN_JS)
    if not spot:
        raise RuntimeError("Jobright's list has no sort menu any more")
    if spot["now"].strip().lower() == want.lower():
        return
    await _click(page, spot)
    for _ in range(10):
        await asyncio.sleep(0.5)
        option = await page.evaluate(f"({SORT_PICK_JS})({json.dumps(want)})")
        if isinstance(option, dict):
            await _click(page, option)
            await asyncio.sleep(3)
            return
    raise RuntimeError(f"Jobright's sort menu has no {want!r} (it lists {option})")


async def _read(page, limit: int) -> list[dict]:
    cards = await page.evaluate(CARDS_JS)
    for _ in range(12):
        if len(cards) >= limit:
            break
        await page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'instant'})")
        await asyncio.sleep(1.5)
        more = await page.evaluate(CARDS_JS)
        if len(more) <= len(cards):
            break
        cards = more
    return cards[:limit]


async def _fetch(watch: Watch) -> list[Posting]:
    from browser import autofill, chrome
    url = chrome.cdp_url(chrome.port())
    if not chrome.is_listening(url):
        raise RuntimeError("Autopilot's Chrome is not running; Jobright is read in it. Start Autopilot first.")
    args = watch.args or {}
    rows: dict[str, dict] = {}
    target = ""
    try:
        async with autofill.attached(url, URL, background=True) as (page, target):
            await page.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            await autofill.wait_for_load(page)
            await asyncio.sleep(4)
            if "/login" in (await page.evaluate("location.href")):
                raise RuntimeError("Jobright is signed out in Autopilot's Chrome; sign in there and check again")
            for sort in args.get("sorts") or SORTS:
                await _sort(page, sort)
                for card in await _read(page, int(args.get("max") or MAX_CARDS)):
                    card.setdefault("lists", [])
                    row = rows.setdefault(card["id"], card)
                    row.setdefault("lists", []).append(sort)
    finally:
        if target:
            chrome.close_tab(target)
    return to_postings(list(rows.values()), int(args.get("days") or DAYS))


def to_postings(cards: list[dict], days: int = DAYS, now: datetime | None = None) -> list[Posting]:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for c in cards:
        posted = age_to_date(c.get("age", ""), now)
        if posted and posted < cutoff:
            continue
        # Only which list the role was on: Jobright's own match and labels are
        # not used ("don't use any information from Jobright", 2026-10-23);
        # the fit is Autopilot's own, worked out like every other role.
        note = "Jobright " + " + ".join(sorted(set(c.get("lists") or [])))
        out.append(Posting(id=c["id"], title=c.get("title", ""), url=c["url"], location=c.get("place", ""),
                           posted=posted, company=c.get("company", ""), text=""))
        out[-1].note = note.strip(" ·")
    out.sort(key=lambda p: p.posted, reverse=True)
    return out


def fetch(watch: Watch, http=None) -> list[Posting]:
    return asyncio.run(_fetch(watch))
