import os
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

AMAZON_LINKS = [
    {
        "name": "Amazon Canada jobs app",
        "url": "https://hiring.amazon.ca/app"
    },
    {
        "name": "Warehouse jobs page",
        "url": "https://hiring.amazon.ca/job-opportunities/warehouse-jobs"
    },
    {
        "name": "Fulfillment Centre Associate",
        "url": "https://hiring.amazon.ca/job-opportunities/fulfillment-centre-associate"
    },
    {
        "name": "Sort Centre Associate",
        "url": "https://hiring.amazon.ca/job-opportunities/sortation-centre-associate"
    },
    {
        "name": "Kitchener Delivery Station Warehouse Associate",
        "url": "https://hiring.amazon.ca/jobDetail/en-CA/Amazon-Delivery-Station-Warehouse-Associate/Kitchener/a0R4U00000MZ3jhUAD"
    },
    {
        "name": "Hamilton Fulfilment Centre Warehouse Associate",
        "url": "https://hiring.amazon.ca/jobDetail/en-CA/Amazon-Fulfilment-Centre-Warehouse-Associate/Hamilton/a0R4U00000MZ3jXUAT"
    },
    {
        "name": "Mississauga Fulfilment Centre Warehouse Associate",
        "url": "https://hiring.amazon.ca/jobDetail/en-CA/Amazon-Fulfilment-Centre-Warehouse-Associate/Mississauga/a0R4U00000NXzbyUAD"
    }
]

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text
    })
    print(response.status_code, response.text)

def check_link(job):
    try:
        response = requests.get(
            job["url"],
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )

        if response.status_code == 200:
            text = response.text.lower()

            if "apply" in text or "warehouse" in text or "amazon" in text:
                message = (
                    "🔔 Amazon job page is available\n\n"
                    f"{job['name']}\n"
                    f"{job['url']}\n\n"
                    "Check quickly and apply if shifts are open."
                )
                send_message(message)

        else:
            print(f"Not available: {job['name']} - {response.status_code}")

    except Exception as e:
        print(f"Error checking {job['name']}: {e}")

def main():
    for job in AMAZON_LINKS:
        check_link(job)

if __name__ == "__main__":
    main()
