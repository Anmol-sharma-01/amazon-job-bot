import asyncio
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from playwright.async_api import Browser, Page, async_playwright

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
SCAN_GROUP = os.getenv("SCAN_GROUP", "logistics").strip().lower()
RUN_REASON = os.getenv("RUN_REASON", "schedule").strip().lower()

SOURCES_FILE = Path("sources.json")
STATE_FILE = Path(f"state_{SCAN_GROUP}.json")
DEBUG_FILE = Path(f"debug_{SCAN_GROUP}.json")

MAX_ALERTS_PER_RUN = 25
FIRST_RUN_ALERT_LIMIT = 15
PAGE_TIMEOUT_MS = 60_000

TARGET_LOCATIONS = [
    "Kitchener", "Waterloo", "Cambridge", "Guelph", "Brantford",
    "Woodstock", "Hamilton", "Stoney Creek", "Burlington", "Oakville",
    "Milton", "Mississauga", "Brampton", "Caledon", "Bolton",
    "Georgetown", "Halton Hills", "Vaughan", "Toronto", "Etobicoke",
    "North York", "Scarborough", "Markham", "Richmond Hill"
]

ONTARIO_TERMS = ["Ontario", ", ON", " ON ", "Remote, Canada", "Remote - Canada",
                 "Canada Remote", "Remote, Ontario", "Remote - Ontario"]

WAREHOUSE_WORDS = [
    "warehouse", "package handler", "material handler", "cargo handler",
    "ramp handler", "sorter", "sortation", "preloader", "loader", "unloader",
    "fulfillment associate", "fulfilment associate", "fulfillment centre",
    "fulfilment centre", "delivery station", "dock worker", "receiver",
    "shipping associate", "shipping/receiving", "general labour",
    "general labor", "operations handler", "team member"
]

DRIVER_WORDS = [
    "delivery driver", "package driver", "package car driver", "flex driver",
    "courier", "driver", "linehaul", "line haul", "tractor trailer",
    "owner operator", "vehicle shifter", "switcher", "az driver",
    "dz driver", "class g"
]

IT_SUPPORT_WORDS = [
    "help desk", "helpdesk", "service desk", "technical support",
    "it support", "desktop support", "support analyst", "support technician",
    "technical support representative", "systems support", "system support",
    "application support", "end user support", "field support",
    "computer technician", "it analyst", "technology support",
    "noc analyst", "operations administrator", "junior system administrator",
    "junior systems administrator"
]

CYBER_WORDS = [
    "cybersecurity", "cyber security", "security analyst", "soc analyst",
    "security operations", "information security", "threat intelligence",
    "threat analyst", "vulnerability analyst", "vulnerability management",
    "incident response", "security incident", "grc analyst",
    "governance risk compliance", "identity access", "iam analyst",
    "security coordinator", "security administrator", "security specialist",
    "security operations centre", "security operations center"
]

SENIOR_WORDS = [
    "senior", " sr ", "sr.", "lead ", "principal", "manager", "director",
    "architect", "staff ", "head of", "vice president", "vp ", "level 3",
    "level iii"
]

GENERIC_TITLES = [
    "search jobs", "view jobs", "view all jobs", "career search",
    "warehouse jobs", "driver jobs", "driving jobs", "information technology",
    "professional jobs", "package handler jobs", "hourly roles",
    "join our talent community", "see open positions", "open opportunities",
    "apply now", "learn more", "job search"
]

DETAIL_PATTERNS = [
    "/job/", "/jobs/", "/jobdetail/", "/job-details/", "/job-description/",
    "jobid=", "requisition", "/position/", "/positions/"
]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message[:4090],
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    print("Telegram:", response.status_code, response.text[:300])
    response.raise_for_status()


def load_sources() -> list[dict[str, Any]]:
    with SOURCES_FILE.open("r", encoding="utf-8") as file:
        sources = json.load(file)
    return [source for source in sources if source.get("group") == SCAN_GROUP]


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"seen": {}, "last_daily_status": ""}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        data.setdefault("seen", {})
        data.setdefault("last_daily_status", "")
        return data
    except (json.JSONDecodeError, OSError):
        return {"seen": {}, "last_daily_status": ""}


def save_state(state: dict[str, Any]) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)


def contains_any(text: str, words: list[str]) -> bool:
    lowered = f" {text.lower()} "
    return any(word.lower() in lowered for word in words)


def location_allowed(text: str, category: str, source: dict[str, Any]) -> bool:
    lowered = text.lower()

    if any(location.lower() in lowered for location in TARGET_LOCATIONS):
        return True

    # IT and cybersecurity alerts may include remote Ontario/Canada roles.
    if category in {"it_support", "cyber"}:
        if any(term.lower() in lowered for term in ONTARIO_TERMS):
            return True

    # Some official pages are already restricted to GTA/Southwestern Ontario,
    # but their cards may omit the location until the detail page opens.
    return bool(source.get("regional_page"))


def classify(text: str, allowed_categories: list[str]) -> str | None:
    lowered = f" {text.lower()} "

    checks = [
        ("warehouse", WAREHOUSE_WORDS),
        ("driver", DRIVER_WORDS),
        ("it_support", IT_SUPPORT_WORDS),
        ("cyber", CYBER_WORDS),
    ]

    for category, words in checks:
        if category not in allowed_categories:
            continue
        if contains_any(lowered, words):
            if category in {"it_support", "cyber"} and contains_any(lowered, SENIOR_WORDS):
                continue
            if category in {"warehouse", "driver"} and contains_any(
                lowered, ["manager", "supervisor", "director", "mechanic"]
            ):
                continue
            return category

    return None


def is_generic_title(title: str) -> bool:
    lowered = clean(title).lower()
    if not lowered or len(lowered) < 4 or len(lowered) > 180:
        return True
    return any(lowered == item or lowered.startswith(item + " ") for item in GENERIC_TITLES)


def is_detail_url(url: str, source_url: str) -> bool:
    if not url or url.rstrip("/") == source_url.rstrip("/"):
        return False

    lowered = url.lower()
    if not any(pattern in lowered for pattern in DETAIL_PATTERNS):
        return False

    path = urlparse(url).path.rstrip("/").lower()
    generic_endings = [
        "/jobs", "/search-results", "/warehouse-workers", "/hourly-roles",
        "/package-delivery-driver", "/career-areas/package-handler/jobs",
        "/career-areas/driver/jobs", "/career-areas/professional/jobs",
        "/viewalljobs"
    ]
    return not any(path.endswith(ending) for ending in generic_endings)


def extract_title(anchor_text: str, card_text: str,
                  allowed_categories: list[str]) -> tuple[str, str] | None:
    candidates = [clean(anchor_text)]
    candidates.extend(clean(line) for line in card_text.splitlines())

    for candidate in candidates:
        if is_generic_title(candidate):
            continue
        category = classify(candidate, allowed_categories)
        if category:
            return candidate[:180], category

    # Sometimes the title and category keyword are split across lines.
    combined = clean(card_text)
    category = classify(combined, allowed_categories)
    if category and candidates:
        for candidate in candidates:
            if not is_generic_title(candidate):
                return candidate[:180], category

    return None


def job_key(company: str, url: str, title: str, location: str) -> str:
    raw = f"{company}|{url}|{title}|{location}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


async def accept_cookies(page: Page) -> None:
    patterns = [
        re.compile(r"accept( all)?", re.I),
        re.compile(r"allow all", re.I),
        re.compile(r"agree", re.I),
    ]
    for pattern in patterns:
        try:
            button = page.get_by_role("button", name=pattern).first
            if await button.count() and await button.is_visible():
                await button.click(timeout=2_000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass


async def expand_results(page: Page) -> None:
    for _ in range(4):
        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(700)

    button_patterns = [
        re.compile(r"load more", re.I),
        re.compile(r"show more", re.I),
        re.compile(r"view more", re.I),
    ]

    for _ in range(3):
        clicked = False
        for pattern in button_patterns:
            try:
                button = page.get_by_role("button", name=pattern).first
                if await button.count() and await button.is_visible():
                    await button.click(timeout=3_000)
                    await page.wait_for_timeout(2_000)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            break


async def extract_page_jobs(page: Page, source: dict[str, Any]) -> list[dict[str, str]]:
    items = await page.locator("a[href]").evaluate_all(
        """
        anchors => anchors.map(a => {
            const card = a.closest(
                "article, li, [data-job-id], [data-testid*='job'], " +
                "[class*='job-card'], [class*='jobCard'], [class*='search-result']"
            ) || a.parentElement || a;
            return {
                text: (a.innerText || a.textContent || "").trim(),
                href: a.href || a.getAttribute("href") || "",
                cardText: (card.innerText || card.textContent || "").trim()
            };
        })
        """
    )

    results: list[dict[str, str]] = []
    for item in items:
        href = clean(item.get("href", ""))
        if href:
            href = urljoin(source["url"], href)

        if not is_detail_url(href, source["url"]):
            continue

        card_text = clean(item.get("cardText", ""))[:1500]
        title_result = extract_title(
            item.get("text", ""),
            card_text,
            source["categories"],
        )
        if not title_result:
            continue

        title, category = title_result
        combined = clean(f"{title} {card_text}")

        if not location_allowed(combined, category, source):
            continue

        location = next(
            (location for location in TARGET_LOCATIONS
             if location.lower() in combined.lower()),
            "Remote/Ontario" if category in {"it_support", "cyber"}
            else source.get("region_name", "Nearby Ontario")
        )

        results.append(
            {
                "company": source["company"],
                "source": source["name"],
                "title": title,
                "category": category,
                "location": location,
                "url": href,
            }
        )

    return results


async def next_page(page: Page) -> bool:
    selectors = [
        "a[rel='next']",
        "a[aria-label*='next' i]",
        "button[aria-label*='next' i]",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() and await locator.is_visible():
                disabled = await locator.get_attribute("disabled")
                aria_disabled = await locator.get_attribute("aria-disabled")
                if disabled is not None or aria_disabled == "true":
                    continue
                old_url = page.url
                await locator.click(timeout=4_000)
                await page.wait_for_timeout(2_500)
                return page.url != old_url or True
        except Exception:
            pass

    try:
        locator = page.get_by_role("link", name=re.compile(r"^next$", re.I)).first
        if await locator.count() and await locator.is_visible():
            await locator.click(timeout=4_000)
            await page.wait_for_timeout(2_500)
            return True
    except Exception:
        pass

    return False


async def scrape_source(browser: Browser, source: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    page = await browser.new_page()
    diagnostics: dict[str, Any] = {
        "source": source["name"],
        "url": source["url"],
        "status": "ok",
        "pages_checked": 0,
        "matches": 0,
        "page_text_length": 0,
    }
    jobs: list[dict[str, str]] = []

    try:
        print(f"Checking {source['name']}: {source['url']}")
        await page.goto(
            source["url"],
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT_MS,
        )
        await page.wait_for_timeout(source.get("wait_ms", 4_000))
        await accept_cookies(page)

        max_pages = int(source.get("max_pages", 1))
        for page_number in range(max_pages):
            await expand_results(page)
            body_text = await page.locator("body").inner_text()
            diagnostics["page_text_length"] = max(
                diagnostics["page_text_length"], len(body_text)
            )
            page_jobs = await extract_page_jobs(page, source)
            jobs.extend(page_jobs)
            diagnostics["pages_checked"] += 1

            if page_number + 1 >= max_pages or not await next_page(page):
                break

        diagnostics["matches"] = len(jobs)

    except Exception as error:
        diagnostics["status"] = "error"
        diagnostics["error"] = f"{type(error).__name__}: {error}"
        print(f"ERROR {source['name']}: {error}")
    finally:
        await page.close()

    return jobs, diagnostics


def format_category(category: str) -> str:
    return {
        "warehouse": "Warehouse",
        "driver": "Driver/Courier",
        "it_support": "IT/Technical Support",
        "cyber": "Cybersecurity",
    }.get(category, category)


def format_job(job: dict[str, str]) -> str:
    return (
        "🚨 New official job posting\n\n"
        f"Company: {job['company']}\n"
        f"Role: {job['title']}\n"
        f"Category: {format_category(job['category'])}\n"
        f"Location: {job['location']}\n"
        f"Source: {job['source']}\n\n"
        f"Apply on the official career page:\n{job['url']}"
    )


async def run() -> None:
    sources = load_sources()
    state = load_state()
    first_run = not bool(state["seen"])

    all_jobs: list[dict[str, str]] = []
    diagnostics: list[dict[str, Any]] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        for source in sources:
            jobs, source_diagnostics = await scrape_source(browser, source)
            all_jobs.extend(jobs)
            diagnostics.append(source_diagnostics)
        await browser.close()

    unique_jobs: dict[str, dict[str, str]] = {}
    for job in all_jobs:
        key = job_key(job["company"], job["url"], job["title"], job["location"])
        job["key"] = key
        unique_jobs[key] = job

    new_jobs = [
        job for key, job in unique_jobs.items()
        if key not in state["seen"]
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    for key, job in unique_jobs.items():
        if key not in state["seen"]:
            state["seen"][key] = {
                "title": job["title"],
                "company": job["company"],
                "url": job["url"],
                "first_seen": now_iso,
            }

    # Keep the state file from growing forever.
    if len(state["seen"]) > 3000:
        ordered = sorted(
            state["seen"].items(),
            key=lambda item: item[1].get("first_seen", ""),
            reverse=True,
        )
        state["seen"] = dict(ordered[:3000])

    errors = [item for item in diagnostics if item["status"] == "error"]
    debug_payload = {
        "scan_group": SCAN_GROUP,
        "run_reason": RUN_REASON,
        "sources_checked": len(sources),
        "matching_jobs_seen_this_run": len(unique_jobs),
        "new_jobs": len(new_jobs),
        "diagnostics": diagnostics,
    }
    DEBUG_FILE.write_text(json.dumps(debug_payload, indent=2), encoding="utf-8")

    alert_jobs = new_jobs[:MAX_ALERTS_PER_RUN]
    if first_run:
        alert_jobs = new_jobs[:FIRST_RUN_ALERT_LIMIT]

    if alert_jobs:
        intro = (
            f"✅ {SCAN_GROUP.title()} scan completed.\n"
            f"Found {len(new_jobs)} new matching role(s) on official career pages."
        )
        if first_run and len(new_jobs) > FIRST_RUN_ALERT_LIMIT:
            intro += (
                f"\nShowing the first {FIRST_RUN_ALERT_LIMIT}; "
                "all current matches have been saved to prevent duplicates."
            )
        send_telegram(intro)
        for job in alert_jobs:
            send_telegram(format_job(job))
    else:
        today = date.today().isoformat()
        should_send_status = (
            RUN_REASON == "workflow_dispatch"
            or state.get("last_daily_status") != today
        )
        if should_send_status:
            message = (
                f"✅ {SCAN_GROUP.title()} job bot checked successfully.\n\n"
                f"Official career sources checked: {len(sources)}\n"
                f"Matching active job links detected: {len(unique_jobs)}\n"
                "New jobs since the previous check: 0"
            )
            if errors:
                message += (
                    f"\n\n⚠️ {len(errors)} source(s) had a loading error. "
                    f"See the GitHub Actions debug artifact: {DEBUG_FILE.name}"
                )
            send_telegram(message)
            state["last_daily_status"] = today

    save_state(state)
    print(json.dumps(debug_payload, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
