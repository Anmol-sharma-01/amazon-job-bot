import os
import json
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
SEEN_FILE = "seen_jobs.json"

LOCATION_PAGES = [
    "https://hiring.amazon.ca/locations/toronto-jobs",
    "https://hiring.amazon.ca/locations/southwest-ontario-jobs",
    "https://hiring.amazon.ca/job-opportunities/warehouse-jobs",
    "https://hiring.amazon.ca/job-opportunities/fulfillment-centre-associate",
    "https://hiring.amazon.ca/job-opportunities/sortation-centre-associate",
    "https://hiring.amazon.ca/job-opportunities/delivery-station-associate",
]

TARGET_CITIES = [
    "Kitchener", "Waterloo", "Cambridge", "Guelph",
    "Hamilton", "Stoney Creek", "Burlington", "Oakville",
    "Milton", "Mississauga", "Brampton", "Toronto",
    "Etobicoke", "Scarborough", "Vaughan", "Bolton",
    "Woodstock", "Brantford"
]

JOB_KEYWORDS = [
    "warehouse",
    "fulfillment",
    "fulfilment",
    "sortation",
    "sort centre",
    "delivery station",
    "associate",
    "package sorter",
    "team member"
]

BAD_KEYWORDS = [
    "software",
    "engineer",
    "manager",
    "corporate",
    "intern",
    "specialist",
    "loss prevention",
    "safety"
]

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20
    )
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

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()

def is_target_job(text):
    lower = text.lower()
    good = any(k in lower for k in JOB_KEYWORDS)
    bad = any(k in lower for k in BAD_KEYWORDS)
    city_match = any(city.lower() in lower for city in TARGET_CITIES)
    return good and not bad and city_match

def find_city(text):
    for city in TARGET_CITIES:
        if city.lower() in text.lower():
            return city
    return "Nearby Ontario"

def scrape_page(url):
    print("Checking:", url)

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(url, headers=headers, timeout=30)
    print("Status:", response.status_code)

    if response.status_code != 200:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    jobs = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = clean(a.get_text(" ", strip=True))

        parent_text = ""
        parent = a.parent
        if parent:
            parent_text = clean(parent.get_text(" ", strip=True))

        combined = f"{text} {parent_text} {href}"

        if not is_target_job(combined):
            continue

        full_url = href if href.startswith("http") else "https://hiring.amazon.ca" + href

        jobs.append({
            "id": full_url,
            "title": text if text else "Amazon warehouse job",
            "city": find_city(combined),
            "url": full_url
        })

    return jobs

def main():
    seen = load_seen()
    all_jobs = []

    for page in LOCATION_PAGES:
        try:
            all_jobs.extend(scrape_page(page))
        except Exception as e:
            print("Error:", page, e)

    unique_jobs = {job["id"]: job for job in all_jobs}
    jobs = list(unique_jobs.values())

    print("Jobs found:", len(jobs))

    new_jobs = []

    for job in jobs:
        if job["id"] not in seen:
            seen.add(job["id"])
            new_jobs.append(job)

    save_seen(seen)

    if not new_jobs:
        send_message("✅ Amazon bot checked. No new warehouse jobs found right now.")
        return

    for job in new_jobs[:10]:
        send_message(
            "🚨 Amazon warehouse job found!\n\n"
            f"Title: {job['title']}\n"
            f"Location: {job['city']}\n\n"
            f"Apply here:\n{job['url']}"
        )

if __name__ == "__main__":
    main()