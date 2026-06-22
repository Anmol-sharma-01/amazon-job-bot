import os
import json
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SEEN_FILE = "seen_jobs.json"

LOCATIONS = [
    "Kitchener", "Waterloo", "Cambridge",
    "Mississauga", "Hamilton", "Toronto",
    "Brampton", "Milton", "Vaughan",
    "Burlington", "Oakville", "Etobicoke", "Scarborough"
]

KEYWORDS = [
    "warehouse",
    "fulfilment",
    "fulfillment",
    "delivery station",
    "sortation",
    "sort centre",
    "associate"
]

def load_seen_jobs():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(json.load(f))

def save_seen_jobs(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f, indent=2)

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text})

def find_jobs_for_city(city):
    search_url = f"https://hiring.amazon.ca/search?query=warehouse&location={city}%2C%20ON"
    response = requests.get(search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)

    soup = BeautifulSoup(response.text, "html.parser")
    jobs = []

    for link in soup.find_all("a", href=True):
        title = link.get_text(" ", strip=True)
        href = link["href"]

        if not title:
            continue

        title_lower = title.lower()

        if any(keyword in title_lower for keyword in KEYWORDS):
            job_url = href if href.startswith("http") else "https://hiring.amazon.ca" + href

            jobs.append({
                "title": title,
                "city": city,
                "url": job_url
            })

    return jobs

def main():
    seen = load_seen_jobs()
    new_jobs_found = 0

    for city in LOCATIONS:
        try:
            jobs = find_jobs_for_city(city)

            for job in jobs:
                job_id = job["url"]

                if job_id not in seen:
                    seen.add(job_id)
                    new_jobs_found += 1

                    message = (
                        "🚨 New Amazon warehouse job found!\n\n"
                        f"Title: {job['title']}\n"
                        f"Location: {job['city']}, ON\n\n"
                        f"Apply here:\n{job['url']}"
                    )

                    send_message(message)

        except Exception as e:
            print(f"Error checking {city}: {e}")

    save_seen_jobs(seen)
    print(f"New jobs found: {new_jobs_found}")

if __name__ == "__main__":
    main()
