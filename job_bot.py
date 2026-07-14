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

PAGE_TIMEOUT_MS = 60_000
MAX_ALERTS_PER_RUN = 25
FIRST_RUN_ALERT_LIMIT = 15


def words(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


TARGET_LOCATIONS = words(
    "Kitchener|Waterloo|Cambridge|Guelph|Brantford|Woodstock|Hamilton|"
    "Stoney Creek|Burlington|Oakville|Milton|Mississauga|Brampton|Caledon|"
    "Bolton|Georgetown|Halton Hills|Vaughan|Toronto|Etobicoke|North York|"
    "Scarborough|Markham|Richmond Hill"
)

REMOTE_CANADA_PATTERNS = [
    r"\bremote\b.{0,60}\bcanada\b",
    r"\bcanada\b.{0,60}\bremote\b",
    r"\bremote\b.{0,60}\bontario\b",
    r"\bontario\b.{0,60}\bremote\b",
]

ONTARIO_PATTERNS = [
    r"\bontario\b",
    r",\s*on\b",
    r"\bon,\s*canada\b",
]

CANADA_PATTERNS = [
    r"\bcanada\b",
]

FOREIGN_LOCATION_PATTERNS = [
    rf"\b{re.escape(item)}\b"
    for item in words(
        "germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|"
        "cologne|düsseldorf|stuttgart|united states|usa|california|texas|"
        "florida|new york|seattle|washington|massachusetts|virginia|"
        "united kingdom|uk|england|london|scotland|ireland|india|"
        "bengaluru|bangalore|hyderabad|pune|mumbai|france|paris|spain|"
        "madrid|italy|netherlands|poland|romania|czech republic|australia|"
        "singapore|mexico|brazil|japan|china"
    )
] + [
    r"\bu\.s\.a?\b",
    r"\bu\.k\.\b",
]

WAREHOUSE_WORDS = words(
    "warehouse|package handler|material handler|cargo handler|ramp handler|"
    "sorter|sortation|preloader|loader|unloader|fulfillment associate|"
    "fulfilment associate|fulfillment centre|fulfilment centre|"
    "delivery station|dock worker|receiver|shipping associate|"
    "shipping/receiving|general labour|general labor|operations handler|"
    "team member"
)

DRIVER_WORDS = words(
    "delivery driver|package driver|package car driver|flex driver|courier|"
    "driver|linehaul|line haul|tractor trailer|owner operator|"
    "vehicle shifter|switcher|az driver|dz driver|class g"
)

IT_SUPPORT_WORDS = words(
    "help desk|helpdesk|service desk|technical support|it support|"
    "desktop support|support analyst|support technician|"
    "technical support representative|systems support|system support|"
    "application support|end user support|field support|computer technician|"
    "it analyst|technology support|noc analyst|operations administrator|"
    "junior system administrator|junior systems administrator"
)

CYBER_WORDS = words(
    "cybersecurity|cyber security|security analyst|soc analyst|"
    "security operations|information security|threat intelligence|"
    "threat analyst|vulnerability analyst|vulnerability management|"
    "incident response|security incident|grc analyst|"
    "governance risk compliance|identity access|iam analyst|"
    "security coordinator|security administrator|security specialist|"
    "security operations centre|security operations center"
)

SENIOR_WORDS = words(
    "senior| sr |sr.|lead |principal|manager|director|architect|staff |"
    "head of|vice president|vp |level 3|level iii"
)

GENERIC_TITLES = set(
    words(
        "search jobs|view jobs|view all jobs|career search|warehouse jobs|"
        "driver jobs|driving jobs|information technology|professional jobs|"
        "package handler jobs|hourly roles|join our talent community|"
        "see open positions|open opportunities|apply now|learn more|job search"
    )
)

DETAIL_PATTERNS = words(
    "/job/|/jobs/|/jobdetail/|/job-details/|/job-description/|jobid=|"
    "requisition|/position/|/positions/"
)


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def regex_any(text: str, patterns: list[str]) -> bool:
    return any(
        re.search(pattern, text, flags=re.I)
        for pattern in patterns
    )


def send_telegram(message: str) -> None:
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
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

    return [
        source
        for source in sources
        if source.get("group") == SCAN_GROUP
    ]


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {
            "seen": {},
            "last_daily_status": "",
        }

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        state.setdefault("seen", {})
        state.setdefault("last_daily_status", "")

        return state

    except (OSError, json.JSONDecodeError):
        return {
            "seen": {},
            "last_daily_status": "",
        }


def save_state(state: dict[str, Any]) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            indent=2,
            sort_keys=True,
        )


def contains_word(text: str, values: list[str]) -> bool:
    lowered = f" {text.lower()} "

    return any(
        value.lower() in lowered
        for value in values
    )


def find_target_city(text: str) -> str | None:
    lowered = text.lower()

    for city in TARGET_LOCATIONS:
        if re.search(
            rf"\b{re.escape(city.lower())}\b",
            lowered,
        ):
            return city

    return None


def is_explicit_ontario(text: str) -> bool:
    return regex_any(
        clean(text).lower(),
        ONTARIO_PATTERNS,
    )


def is_explicit_canada(text: str) -> bool:
    return regex_any(
        clean(text).lower(),
        CANADA_PATTERNS,
    )


def is_remote_canada(text: str) -> bool:
    return regex_any(
        clean(text).lower(),
        REMOTE_CANADA_PATTERNS,
    )


def has_foreign_location(text: str) -> bool:
    return regex_any(
        clean(text).lower(),
        FOREIGN_LOCATION_PATTERNS,
    )


def location_allowed(
    text: str,
    category: str,
    source: dict[str, Any],
) -> bool:

    # Reject Germany, the United States, India and other foreign locations.
    if has_foreign_location(text):
        return False

    city = find_target_city(text)

    if city:
        # This prevents locations such as Cambridge, United Kingdom
        # from being accepted as Cambridge, Ontario.
        return bool(
            is_explicit_ontario(text)
            or is_explicit_canada(text)
            or source.get("regional_page")
        )

    # IT support and cybersecurity roles may be located elsewhere in Ontario.
    if category in {"it_support", "cyber"}:

        # Remote is accepted only when Canada or Ontario is explicitly listed.
        if is_remote_canada(text):
            return True

        # Accept onsite or hybrid positions elsewhere in Ontario.
        if (
            is_explicit_ontario(text)
            and "remote" not in text.lower()
        ):
            return True

    # Only trusted Ontario regional pages can use this fallback.
    return bool(source.get("regional_page"))


def classify(
    text: str,
    allowed_categories: list[str],
) -> str | None:

    checks = [
        ("warehouse", WAREHOUSE_WORDS),
        ("driver", DRIVER_WORDS),
        ("it_support", IT_SUPPORT_WORDS),
        ("cyber", CYBER_WORDS),
    ]

    for category, values in checks:

        if category not in allowed_categories:
            continue

        if not contains_word(text, values):
            continue

        if (
            category in {"it_support", "cyber"}
            and contains_word(text, SENIOR_WORDS)
        ):
            continue

        if (
            category in {"warehouse", "driver"}
            and contains_word(
                text,
                [
                    "manager",
                    "supervisor",
                    "director",
                    "mechanic",
                ],
            )
        ):
            continue

        return category

    return None


def is_generic_title(title: str) -> bool:
    lowered = clean(title).lower()

    return (
        not lowered
        or len(lowered) < 4
        or len(lowered) > 180
        or lowered in GENERIC_TITLES
    )


def is_detail_url(
    url: str,
    source_url: str,
) -> bool:

    if not url:
        return False

    if url.rstrip("/") == source_url.rstrip("/"):
        return False

    if not any(
        pattern in url.lower()
        for pattern in DETAIL_PATTERNS
    ):
        return False

    path = urlparse(url).path.rstrip("/").lower()

    generic_endings = words(
        "/jobs|/search-results|/warehouse-workers|/hourly-roles|"
        "/package-delivery-driver|/career-areas/package-handler/jobs|"
        "/career-areas/driver/jobs|/career-areas/professional/jobs|"
        "/viewalljobs"
    )

    return not any(
        path.endswith(ending)
        for ending in generic_endings
    )


def extract_title(
    anchor_text: str,
    card_text: str,
    categories: list[str],
) -> tuple[str, str] | None:

    candidates = [clean(anchor_text)]

    candidates.extend(
        clean(line)
        for line in card_text.splitlines()
    )

    for candidate in candidates:

        if is_generic_title(candidate):
            continue

        category = classify(
            candidate,
            categories,
        )

        if category:
            return candidate[:180], category

    category = classify(
        clean(card_text),
        categories,
    )

    if category:
        for candidate in candidates:
            if not is_generic_title(candidate):
                return candidate[:180], category

    return None


def make_job_key(job: dict[str, str]) -> str:
    raw = (
        f"{job['company']}|"
        f"{job['url']}|"
        f"{job['title']}|"
        f"{job['location']}"
    ).lower()

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]


async def accept_cookies(page: Page) -> None:
    patterns = [
        re.compile(r"accept( all)?", re.I),
        re.compile(r"allow all", re.I),
        re.compile(r"agree", re.I),
    ]

    for pattern in patterns:
        try:
            button = page.get_by_role(
                "button",
                name=pattern,
            ).first

            if (
                await button.count()
                and await button.is_visible()
            ):
                await button.click(timeout=2_000)
                await page.wait_for_timeout(500)
                return

        except Exception:
            pass


async def expand_results(page: Page) -> None:
    for _ in range(4):
        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(700)

    patterns = [
        re.compile(r"load more", re.I),
        re.compile(r"show more", re.I),
        re.compile(r"view more", re.I),
    ]

    for _ in range(3):
        clicked = False

        for pattern in patterns:
            try:
                button = page.get_by_role(
                    "button",
                    name=pattern,
                ).first

                if (
                    await button.count()
                    and await button.is_visible()
                ):
                    await button.click(timeout=3_000)
                    await page.wait_for_timeout(2_000)
                    clicked = True
                    break

            except Exception:
                pass

        if not clicked:
            break


async def extract_page_jobs(
    page: Page,
    source: dict[str, Any],
) -> list[dict[str, str]]:

    items = await page.locator("a[href]").evaluate_all(
        """
        anchors => anchors.map(a => {
            const anchorText =
                (a.innerText || a.textContent || "").trim();

            const candidates = [
                a.closest(
                    "article, li, [data-job-id], " +
                    "[data-testid*='job'], " +
                    "[class*='job-card'], " +
                    "[class*='jobCard'], " +
                    "[class*='search-result']"
                ),
                a.parentElement,
                a.parentElement
                    ? a.parentElement.parentElement
                    : null,
                a.parentElement && a.parentElement.parentElement
                    ? a.parentElement.parentElement.parentElement
                    : null
            ].filter(Boolean);

            /*
            Use the smallest useful job-card container.

            This prevents global page text such as "Canada jobs"
            from making a Germany job appear Canadian.
            */
            let cardText = anchorText;

            for (const element of candidates) {
                const candidateText =
                    (
                        element.innerText ||
                        element.textContent ||
                        ""
                    ).trim();

                if (
                    candidateText.length >= anchorText.length &&
                    candidateText.length <= 1800
                ) {
                    cardText = candidateText;
                    break;
                }
            }

            return {
                text: anchorText,
                href:
                    a.href ||
                    a.getAttribute("href") ||
                    "",
                cardText
            };
        })
        """
    )

    jobs: list[dict[str, str]] = []

    for item in items:

        href = urljoin(
            source["url"],
            clean(item.get("href", "")),
        )

        if not is_detail_url(
            href,
            source["url"],
        ):
            continue

        card_text = clean(
            item.get("cardText", "")
        )[:1500]

        title_result = extract_title(
            item.get("text", ""),
            card_text,
            source["categories"],
        )

        if not title_result:
            continue

        title, category = title_result

        combined = clean(
            f"{title} {card_text}"
        )

        if not location_allowed(
            combined,
            category,
            source,
        ):
            continue

        city = find_target_city(combined)

        if city:
            location = city

        elif is_remote_canada(combined):
            location = "Remote — Canada"

        elif is_explicit_ontario(combined):
            location = "Ontario, Canada"

        else:
            location = source.get(
                "region_name",
                "Nearby Ontario",
            )

        jobs.append(
            {
                "company": source["company"],
                "source": source["name"],
                "title": title,
                "category": category,
                "location": location,
                "url": href,
            }
        )

    return jobs


async def click_next(page: Page) -> bool:
    selectors = [
        "a[rel='next']",
        "a[aria-label*='next' i]",
        "button[aria-label*='next' i]",
    ]

    for selector in selectors:
        try:
            button = page.locator(selector).first

            if not await button.count():
                continue

            if not await button.is_visible():
                continue

            if (
                await button.get_attribute("disabled")
                is not None
            ):
                continue

            if (
                await button.get_attribute("aria-disabled")
                == "true"
            ):
                continue

            await button.click(timeout=4_000)
            await page.wait_for_timeout(2_500)

            return True

        except Exception:
            pass

    try:
        link = page.get_by_role(
            "link",
            name=re.compile(r"^next$", re.I),
        ).first

        if (
            await link.count()
            and await link.is_visible()
        ):
            await link.click(timeout=4_000)
            await page.wait_for_timeout(2_500)
            return True

    except Exception:
        pass

    return False


async def scrape_source(
    browser: Browser,
    source: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:

    page = await browser.new_page()

    jobs: list[dict[str, str]] = []

    diagnostic: dict[str, Any] = {
        "source": source["name"],
        "url": source["url"],
        "status": "ok",
        "pages_checked": 0,
        "matches": 0,
        "page_text_length": 0,
    }

    try:
        print(
            f"Checking {source['name']}: "
            f"{source['url']}"
        )

        await page.goto(
            source["url"],
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT_MS,
        )

        await page.wait_for_timeout(
            int(source.get("wait_ms", 4_000))
        )

        await accept_cookies(page)

        max_pages = int(
            source.get("max_pages", 1)
        )

        for page_number in range(max_pages):

            await expand_results(page)

            body_text = await page.locator(
                "body"
            ).inner_text()

            diagnostic["page_text_length"] = max(
                diagnostic["page_text_length"],
                len(body_text),
            )

            page_jobs = await extract_page_jobs(
                page,
                source,
            )

            jobs.extend(page_jobs)

            diagnostic["pages_checked"] += 1

            if page_number + 1 >= max_pages:
                break

            if not await click_next(page):
                break

        diagnostic["matches"] = len(jobs)

    except Exception as error:

        diagnostic["status"] = "error"

        diagnostic["error"] = (
            f"{type(error).__name__}: {error}"
        )

        print(
            f"ERROR {source['name']}: {error}"
        )

    finally:
        await page.close()

    return jobs, diagnostic


def category_name(category: str) -> str:
    return {
        "warehouse": "Warehouse",
        "driver": "Driver/Courier",
        "it_support": "IT/Technical Support",
        "cyber": "Cybersecurity",
    }.get(category, category)


def format_job(job: dict[str, str]) -> str:
    return (
        "🚨 New official Canada job posting\n\n"
        f"Company: {job['company']}\n"
        f"Role: {job['title']}\n"
        f"Category: {category_name(job['category'])}\n"
        f"Location: {job['location']}\n"
        f"Source: {job['source']}\n\n"
        "Apply on the official career page:\n"
        f"{job['url']}"
    )


async def run() -> None:
    sources = load_sources()
    state = load_state()

    first_run = not bool(
        state["seen"]
    )

    all_jobs: list[dict[str, str]] = []
    diagnostics: list[dict[str, Any]] = []

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True
        )

        for source in sources:

            jobs, diagnostic = await scrape_source(
                browser,
                source,
            )

            all_jobs.extend(jobs)
            diagnostics.append(diagnostic)

        await browser.close()

    unique_jobs: dict[str, dict[str, str]] = {}

    for job in all_jobs:

        key = make_job_key(job)

        job["key"] = key

        unique_jobs[key] = job

    new_jobs = [
        job
        for key, job in unique_jobs.items()
        if key not in state["seen"]
    ]

    now = datetime.now(
        timezone.utc
    ).isoformat()

    for key, job in unique_jobs.items():

        state["seen"].setdefault(
            key,
            {
                "title": job["title"],
                "company": job["company"],
                "url": job["url"],
                "first_seen": now,
            },
        )

    if len(state["seen"]) > 3000:

        newest = sorted(
            state["seen"].items(),
            key=lambda item: item[1].get(
                "first_seen",
                "",
            ),
            reverse=True,
        )[:3000]

        state["seen"] = dict(newest)

    errors = [
        item
        for item in diagnostics
        if item["status"] == "error"
    ]

    debug = {
        "scan_group": SCAN_GROUP,
        "run_reason": RUN_REASON,
        "sources_checked": len(sources),
        "matching_jobs_seen_this_run": len(
            unique_jobs
        ),
        "new_jobs": len(new_jobs),
        "diagnostics": diagnostics,
    }

    DEBUG_FILE.write_text(
        json.dumps(
            debug,
            indent=2,
        ),
        encoding="utf-8",
    )

    if first_run:
        alerts = new_jobs[
            :FIRST_RUN_ALERT_LIMIT
        ]
    else:
        alerts = new_jobs[
            :MAX_ALERTS_PER_RUN
        ]

    if alerts:

        intro = (
            f"✅ {SCAN_GROUP.title()} Canada scan completed.\n"
            f"Found {len(new_jobs)} new matching role(s)."
        )

        if (
            first_run
            and len(new_jobs) > FIRST_RUN_ALERT_LIMIT
        ):
            intro += (
                f"\nShowing the first "
                f"{FIRST_RUN_ALERT_LIMIT}; "
                "all matches were saved to "
                "prevent duplicate alerts."
            )

        send_telegram(intro)

        for job in alerts:
            send_telegram(
                format_job(job)
            )

    else:

        today = date.today().isoformat()

        should_send_status = (
            RUN_REASON == "workflow_dispatch"
            or state.get("last_daily_status")
            != today
        )

        if should_send_status:

            message = (
                f"✅ {SCAN_GROUP.title()} Canada job bot "
                "checked successfully.\n\n"
                f"Official career sources checked: "
                f"{len(sources)}\n"
                "Matching active Canada job links detected: "
                f"{len(unique_jobs)}\n"
                "New jobs since the previous check: 0"
            )

            if errors:
                message += (
                    f"\n\n⚠️ {len(errors)} source(s) "
                    "had a loading error. "
                    f"Check: {DEBUG_FILE.name}"
                )

            send_telegram(message)

            state["last_daily_status"] = today

    save_state(state)

    print(
        json.dumps(
            debug,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
