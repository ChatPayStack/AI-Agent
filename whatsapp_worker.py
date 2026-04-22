import os
import json
import redis
import asyncio
import uuid

from shopping_agent import chat_turn
from db import connect_mongo, close_mongo

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
WORKER_ID = os.getenv("WORKER_ID", "whatsapp-worker")
business_id = os.getenv("BUSINESS_ID")

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)


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

                print("\n🤖 Agent response:")
                print(response)

            except Exception as e:
                print(f"[{WORKER_ID}] ERROR:", e)

    finally:
        close_mongo()


if __name__ == "__main__":
    asyncio.run(main())