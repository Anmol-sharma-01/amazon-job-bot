import os
import requests
import asyncio
from playwright.async_api import async_playwright

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text
        }
    )

    print("Telegram:", response.status_code)
    print(response.text)

async def main():
    send_message("🤖 Amazon Job Bot started.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page()

        print("Opening Amazon...")
        await page.goto(
            "https://hiring.amazon.ca/app",
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(10000)

        text = await page.locator("body").inner_text()

        print("Page text length:", len(text))

        send_message(
            f"✅ Amazon page loaded successfully.\n\nPage text length: {len(text)}"
        )

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())