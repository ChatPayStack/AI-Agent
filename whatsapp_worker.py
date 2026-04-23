import os
import json
import redis
import asyncio
import uuid

from shopping_agent import chat_turn
from db import connect_mongo, close_mongo

import httpx

from whatsapp_formatter import format_for_whatsapp

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
WORKER_ID = os.getenv("WORKER_ID", "whatsapp-worker")
business_id = os.getenv("BUSINESS_ID")

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

async def send_whatsapp_message(to: str, msg: dict):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_WHATSAPP_NUMBER")  # e.g. whatsapp:+14155238886

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    payload = {
        "From": from_number,
        "To": to,
    }

    # Text
    if msg["type"] == "text":
        payload["Body"] = msg["content"]

    # Image
    elif msg["type"] == "photo":
        payload["MediaUrl"] = msg["content"]

    else:
        return

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            data=payload,
            auth=(account_sid, auth_token)
        )

        if resp.status_code >= 400:
            print("❌ WhatsApp send error:", resp.status_code, resp.text)
        else:
            print("✅ WhatsApp sent")
            
async def main():
    print(f"[{WORKER_ID}] started")
    print(f"[{WORKER_ID}] listening on chatpay_queue_{business_id}")

    connect_mongo()

    try:
        while True:
            task = r.blpop(f"chatpay_queue_{business_id}", timeout=1)
            if not task:
                continue

            _, raw = task

            try:
                data = json.loads(raw)

                # ✅ Only WhatsApp
                if data.get("channel") != "whatsapp":
                    continue

                print("\n📩 WhatsApp message received:")
                print(json.dumps(data, indent=2))

                message = data.get("message") or {}
                user_text = (message.get("text") or "").strip()
                user_id = message.get("from", {}).get("id")

                if not user_text or not user_id:
                    continue

                thread_id = str(user_id)
                turn_id = str(uuid.uuid4())

                response = await chat_turn(
                    thread_id=thread_id,
                    text=user_text,
                    business_id=business_id,
                    turn_id=turn_id
                )

                envelope = json.loads(response)

                messages = format_for_whatsapp(envelope)

                chat_id = thread_id  # phone number

                for msg in messages:
                    await send_whatsapp_message(chat_id, msg)

            except Exception as e:
                print(f"[{WORKER_ID}] ERROR:", e)

    finally:
        close_mongo()


if __name__ == "__main__":
    asyncio.run(main())