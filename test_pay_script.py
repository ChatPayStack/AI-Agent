import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = 785862166

TELEGRAM_SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

PAY_URL = "https://chat-pay-gateway.vercel.app?address=8hwTWjptyobR8godtYv2iG6YVnHBixhZGxnrbd4pHfCd&amount=57.99&token=HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr&reference=AoEKj8d5oxcv9xHvTxmiZBTFkv2mh2MjjjGCCkXsejPC"


async def send_message():
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TELEGRAM_SEND_URL,
            json={
                "chat_id": CHAT_ID,
                "text": "Tap below to pay 💶",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "💶 Pay with EURC", "url": PAY_URL}]
                    ]
                }
            },
        )

        print(resp.status_code)
        print(resp.text)


if __name__ == "__main__":
    asyncio.run(send_message())