import os
import json
import redis
import asyncio
import uuid

from shopping_agent import chat_turn
from db import connect_mongo, close_mongo
from email_formatter import format_for_email

import httpx

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
WORKER_ID = os.getenv("WORKER_ID", "email-worker")
business_id = os.getenv("BUSINESS_ID")

GRAPH_API_ENDPOINT = os.getenv("GRAPH_API_ENDPOINT", "https://graph.microsoft.com/v1.0")
GRAPH_API_ACCESS_TOKEN = os.getenv("GRAPH_API_ACCESS_TOKEN")

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)


async def send_email_reply(to: str, subject: str, body: str, thread_id: str):
    """Send a reply email via Microsoft Graph API."""
    token = GRAPH_API_ACCESS_TOKEN
    if not token:
        print(f"[{WORKER_ID}] ❌ GRAPH_API_ACCESS_TOKEN not set — cannot send email")
        return

    # Re-use subject prefix if not already present
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

    payload = {
        "message": {
            "subject": reply_subject,
            "body": {
                "contentType": "Text",
                "content": body,
            },
            "toRecipients": [
                {"emailAddress": {"address": to}}
            ],
        },
        "saveToSentItems": True,
    }

    url = f"{GRAPH_API_ENDPOINT}/me/messages/{thread_id}/reply"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                print(f"[{WORKER_ID}] ❌ Email send error ({resp.status_code}): {resp.text}")
            else:
                print(f"[{WORKER_ID}] ✅ Email reply sent to {to}")
        except httpx.RequestError as exc:
            print(f"[{WORKER_ID}] ❌ Email send request failed: {exc}")


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

                # Only email messages
                if data.get("channel") != "email":
                    continue

                print(f"\n[{WORKER_ID}] 📩 Email received:")
                print(json.dumps(data, indent=2))

                message = data.get("message") or {}
                sender_email = message.get("from", "").strip()
                sender_name = message.get("from_name", "").strip()
                subject = message.get("subject", "").strip()
                body = message.get("body", "").strip()
                thread_id = message.get("thread_id", "").strip()

                if not sender_email or not body or not thread_id:
                    print(f"[{WORKER_ID}] ⚠️  Missing required fields (from/body/thread_id) — skipping")
                    continue

                user_text = body
                turn_id = str(uuid.uuid4())

                print(f"[{WORKER_ID}] 🔄 Calling chat_turn | thread={thread_id} | from={sender_email}")

                response = await chat_turn(
                    thread_id=thread_id,
                    text=user_text,
                    business_id=business_id,
                    turn_id=turn_id,
                )

                envelope = json.loads(response)

                print(f"[{WORKER_ID}] 🤖 Agent response (type={envelope.get('type')}):")
                print(json.dumps(envelope, indent=2))

                reply_body = format_for_email(envelope)

                await send_email_reply(
                    to=sender_email,
                    subject=subject,
                    body=reply_body,
                    thread_id=thread_id,
                )

            except Exception as e:
                print(f"[{WORKER_ID}] ERROR: {e}")

    finally:
        close_mongo()


if __name__ == "__main__":
    asyncio.run(main())
