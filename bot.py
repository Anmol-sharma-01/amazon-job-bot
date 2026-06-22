import os
import json
import re
import asyncio
import requests
from playwright.async_api import async_playwright

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SEEN_FILE = "seen_jobs.json"

# Kitchener City Hall area postal code. Bot searches around Kitchener.
POSTAL_CODE = "N2G 4G7"

# 100 km radius target
SEARCH_URL = (
    "https://hiring.amazon.ca/app#jobSearch?"
    "jobType=Full-time%2CPart-time%2CFlex%20Time"
    "&employmentType=Regular%2CSeasonal"
    "&jobTitle=Amazon%20Warehouse%20Associate%2CAmazon%20Fulfillment%20Centre%20Warehouse%20Associate%2CAmazon%20Delivery%20Station%20Warehouse%20Associate%2CAmazon%20Sortation%20Centre%20Warehouse%20Associate%2CAmazon%20XL%20Warehouse%20Associate"
)

TARGET_CITIES = [
    "Kitchener", "Waterloo", "Cambridge", "Guelph",
    "Hamilton", "Stoney Creek", "Burlington", "Oakville",
    "Milton", "Mississauga", "Brampton", "Toronto",
    "Etobicoke", "Scarborough", "Vaughan", "Bolton",
    "Woodstock", "Brantford"
]

WAREHOUSE_KEYWORDS = [
    "warehouse",
    "fulfilment",
    "fulfillment",
    "fulfillment centre",
    "fulfilment centre",
    "delivery station",
    "sortation",
    "sort centre",
    "sortation centre",
    "xl warehouse",
    "team member"
]

BAD_KEYWORDS = [
    "software",
    "engineer",
    "manager",
    "specialist",
    "loss prevention",
    "safety",
    "ehs",
    "whs",
    "intern",
    "corporate"
]

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    }, timeout=20)

    print("Telegram:", response.status_code, response.text)

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, indent=2)

def looks_like_warehouse_job(text):
    text_lower = text.lower()

    has_warehouse_keyword = any(k in text_lower for k in WAREHOUSE_KEYWORDS)
    has_bad_keyword = any(k in text_lower for k in BAD_KEYWORDS)

    return has_warehouse_keyword and not has_bad_keyword

def find_city(text):
    for city in TARGET_CITIES:
        if city.lower() in text.lower():
            return city
    return "Within 100 km of Kitchener"

def clean_text(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:500]

def extract_title(card_text):
    lines = [line.strip() for line in card_text.split("\n") if line.strip()]

    for line in lines:
        if looks_like_warehouse_job(line):
            return clean_text(line)

    for line in lines:
        if "amazon" in line.lower():
            return clean_text(line)

    return clean_text(lines[0]) if lines else "Amazon warehouse job"

def extract_pay(card_text):
    match = re.search(r"\$[0-9]+(?:\.[0-9]{2})?(?:\s*(?:-|to)\s*\$[0-9]+(?:\.[0-9]{2})?)?", card_text)
    return match.group(0) if match else "Pay not shown"

async def try_search_by_postal(page):
    # Try to fill postal/location field if the app shows one.
    inputs = await page.locator("input").element_handles()

    for input_box in inputs:
        try:
            attrs = await input_box.evaluate("""
                el => [
                    el.placeholder || '',
                    el.name || '',
                    el.id || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('data-testid') || ''
                ].join(' ').toLowerCase()
            """)

            if any(word in attrs for word in ["postal", "postcode", "zip", "location", "city"]):
                await input_box.fill(POSTAL_CODE)
                await input_box.press("Enter")
                await page.wait_for_timeout(5000)
                return
        except Exception:
            pass

    # Fallback: try first visible input.
    try:
        first_input = page.locator("input").first()
        if await first_input.count() > 0:
            await first_input.fill(POSTAL_CODE)
            await first_input.press("Enter")
            await page.wait_for_timeout(5000)
    except Exception:
        pass

async def scrape_amazon_jobs():
    jobs = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            geolocation={"latitude": 43.4516, "longitude": -80.4925},
            permissions=["geolocation"],
            locale="en-CA"
        )

        page = await context.new_page()

        print("Opening Amazon hiring page...")
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

        await try_search_by_postal(page)

        # Give Amazon app time to load results.
        await page.wait_for_timeout(8000)

        links = await page.locator("a").evaluate_all("""
            elements => elements.map(a => {
                const parent1 = a.parentElement ? a.parentElement.innerText : "";
                const parent2 = a.parentElement && a.parentElement.parentElement ? a.parentElement.parentElement.innerText : "";
                const parent3 = a.closest("div") ? a.closest("div").innerText : "";

                return {
                    text: (a.innerText || a.textContent || "").trim(),
                    href: a.href || "",
                    cardText: [a.innerText || "", parent1, parent2, parent3].join("\\n")
                };
            })
        """)

        page_text = await page.locator("body").inner_text()
        print("Page text length:", len(page_text))

        for item in links:
            href = item.get("href", "")
            card_text = item.get("cardText", "")

            combined = f"{item.get('text', '')}\n{card_text}\n{href}"

            is_job_link = (
                "jobdetail" in href.lower()
                or "jobid=" in href.lower()
                or "jobDetail" in combined
            )

            if not is_job_link:
                continue

            if not looks_like_warehouse_job(combined):
                continue

            title = extract_title(card_text)
            city = find_city(card_text)
            pay = extract_pay(card_text)

            if not href:
                href = "https://hiring.amazon.ca/app"

            jobs.append({
                "id": href,
                "title": title,
                "city": city,
                "pay": pay,
                "url": href
            })

        # Fallback: if page says warehouse jobs exist but links were not readable.
        if not jobs and looks_like_warehouse_job(page_text):
            jobs.append({
                "id": "amazon-app-fallback",
                "title": "Possible Amazon warehouse job found",
                "city": "Within 100 km of Kitchener",
                "pay": "Check Amazon page",
                "url": "https://hiring.amazon.ca/app"
            })

        await browser.close()

    # Remove duplicates
    unique = {}
    for job in jobs:
        unique[job["id"]] = job

    return list(unique.values())

async def main():
    seen = load_seen()
    jobs = await scrape_amazon_jobs()

    print(f"Jobs found: {len(jobs)}")

    new_jobs = []

    for job in jobs:
        if job["id"] not in seen:
            seen.add(job["id"])
            new_jobs.append(job)

    save_seen(seen)

    if not new_jobs:
    send_message("✅ Amazon Job Bot checked successfully. No new warehouse jobs found right now.")
    print("No new Amazon warehouse jobs found.")
    return

    for job in new_jobs[:10]:
        message = (
            "🚨 New Amazon warehouse job found!\n\n"
            f"Title: {job['title']}\n"
            f"Location: {job['city']}\n"
            f"Pay: {job['pay']}\n\n"
            f"Apply/check here:\n{job['url']}"
        )
        send_message(message)

if __name__ == "__main__":
    asyncio.run(main())
