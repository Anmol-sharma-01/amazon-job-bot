import os
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

LOCATIONS = [
    "Kitchener", "Waterloo", "Cambridge",
    "Mississauga", "Hamilton", "Toronto",
    "Brampton", "Milton", "Vaughan",
    "Burlington", "Oakville", "Etobicoke", "Scarborough"
]

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
    print(response.text)

def check_jobs():
    for city in LOCATIONS:
        url = f"https://hiring.amazon.ca/search?query=warehouse&location={city}%2C%20ON"
        send_message(f"Amazon warehouse jobs near {city}:\n{url}")

if __name__ == "__main__":
    check_jobs()
