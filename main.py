import os
import json
import redis
import asyncio
import httpx
import uuid
from datetime import datetime, timezone

from shopping_agent import chat_turn
from db import connect_mongo, close_mongo, get_categories, payments
from telegram_formatter import format_for_telegram

from payment_check import wait_for_eurc_payment
from payments_store import update_payment_attempt, load_latest_payment

from bson import ObjectId

from orders_store import create_order

from cart_store import load_cart, clear_cart, save_cart

import traceback

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
WORKER_ID = os.getenv("WORKER_ID", "worker-1")
business_id = os.getenv("BUSINESS_ID")

TELEGRAM_SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

REPLY_KEYBOARD = None


async def build_keyboard():
    global REPLY_KEYBOARD

    categories = await get_categories(business_id)

    keyboard = [
        [{"text": c["title"]}]
        for c in categories
        if "title" in c
    ]

    keyboard.append([{"text": "🛒 View Cart"}])
    keyboard.append([{"text": "💳 Checkout"}])

    REPLY_KEYBOARD = {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False
    }


async def send_telegram_message(chat_id: int, msg: dict, worker_id: str, trace_id: str):
    try:
        async with httpx.AsyncClient(timeout=15) as client:

            if msg["type"] == "text":
                url = TELEGRAM_SEND_URL
                payload = {
                    "chat_id": chat_id,
                    "text": msg["content"],
                    "parse_mode": "Markdown",
                    "reply_markup": msg.get("reply_markup") or REPLY_KEYBOARD,
                    "disable_web_page_preview": True
                }

                resp = await client.post(url, json=payload)

            elif msg["type"] == "photo":
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                payload = {
                    "chat_id": chat_id,
                    "photo": msg["content"],
                }

                resp = await client.post(url, json=payload)

            elif msg["type"] == "qr":
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

                files = {
                    "photo": ("qr.png", msg["content"], "image/png")
                }

                data = {
                    "chat_id": str(chat_id)
                }

                resp = await client.post(url, data=data, files=files)

            else:
                return None

            if resp.status_code >= 400:
                print(f"[{worker_id}] TELEGRAM ERROR {resp.status_code}: {resp.text}")
                return None

            # 🔥 Extract message_id
            data = resp.json()
            result = data.get("result", {})

            if msg["type"] == "photo":
                file_id = result.get("photo", [{}])[-1].get("file_id")
                return file_id

            return result.get("message_id")

    except Exception as e:
        print(f"[{worker_id}] SEND ERROR: {repr(e)}")
        return None

async def main():
    print(f"[{WORKER_ID}] started")
    print(f"[{WORKER_ID}] listening on chatpay_queue_{business_id}")

    connect_mongo()
    await build_keyboard()

    try:
        while True:
            task = r.blpop(f"chatpay_queue_{business_id}", timeout=1)
            if not task:
                continue

            _, raw = task

            try:
                data = json.loads(raw)
                if data.get("type") == "stripe_webhook":
                    metadata = data.get("metadata", {})
                    thread_id = metadata.get("thread_id")
                    payment_id = ObjectId(metadata.get("payment_id"))
                    stripe_session_id = data.get("stripe_session_id")

                    # 1️⃣ Mark payment paid
                    await update_payment_attempt(
                        payment_id,
                        business_id,
                        {
                            "stage": "paid",
                            "status": "success",
                            "stripe_session_id": stripe_session_id,
                            "paid_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )

                    payment_doc = await payments(business_id).find_one({"_id": payment_id})

                    # 2️⃣ Create order (idempotent — safe on Stripe retries)
                    order = await create_order({
                        "business_id": business_id,
                        "thread_id": thread_id,
                        "payment_id": payment_id,
                        "stripe_session_id": stripe_session_id,
                        "cart_snapshot": payment_doc.get("cart_snapshot"),
                        "email": payment_doc.get("email"),
                        "address": payment_doc.get("address"),
                        "country": payment_doc.get("country"),
                        "total_amount": payment_doc.get("total_amount"),
                        "currency": "eur",
                        "payment_method": "stripe",
                        "status": "paid",
                    })

                    # 3️⃣ Clear cart
                    cart = await load_cart(thread_id, business_id)
                    clear_cart(cart)
                    await save_cart(cart)

                    # 4️⃣ Send confirmation (Telegram only — thread_id must be numeric)
                    total = payment_doc.get("total_amount", "")
                    total_str = f"€{total}" if total else ""
                    confirm_text = (
                        f"✅ Payment successful!\n\n"
                        f"Your order has been confirmed and is now being processed."
                        + (f"\n\nOrder total: {total_str}" if total_str else "")
                        + f"\n\nThanks for shopping with us. 🛍️"
                    )

                    if str(thread_id).lstrip("+-").isdigit():
                        await send_telegram_message(
                            int(thread_id),
                            {"type": "text", "content": confirm_text},
                            WORKER_ID,
                            "stripe_webhook"
                        )
                    else:
                        print(f"[{WORKER_ID}] ℹ️  Non-Telegram thread_id={thread_id} — skipping Telegram confirmation")

                    continue

                if data.get("type") == "coinbase_webhook":

                    thread_id = data.get("thread_id")
                    payment_id = ObjectId(data.get("payment_id"))

                    # 1️⃣ Mark payment complete
                    await update_payment_attempt(
                        payment_id,
                        business_id,
                        {
                            "stage": "completed",
                            "tx_hash": data.get("tx_hash"),
                            "paid_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )

                    payment_doc = await payments(business_id).find_one({"_id": payment_id})

                    # 2️⃣ Create order
                    await create_order({
                        "business_id": business_id,
                        "thread_id": thread_id,
                        "payment_id": payment_id,
                        "cart_snapshot": payment_doc.get("cart_snapshot"),
                        "email": payment_doc.get("email"),
                        "address": payment_doc.get("address"),
                        "country": payment_doc.get("country"),
                        "amount": payment_doc.get("amount"),
                        "currency": payment_doc.get("currency"),
                    })

                    # 3️⃣ Clear cart
                    cart = await load_cart(thread_id, business_id)
                    clear_cart(cart)
                    await save_cart(cart)

                    # 4️⃣ Send Telegram confirmation
                    await send_telegram_message(
                        int(thread_id),
                        {"type": "text", "content": "Payment successful ✅ Your order has been placed!"},
                        WORKER_ID,
                        "coinbase_webhook"
                    )

                    continue

                if data.get("type") == "stripe_webhook_failed":

                    metadata = data.get("metadata", {})
                    thread_id = metadata.get("thread_id")
                    payment_id = ObjectId(metadata.get("payment_id"))

                    await update_payment_attempt(
                        payment_id,
                        business_id,
                        {"stage": "failed"},
                    )

                    await send_telegram_message(
                        int(thread_id),
                        {"type": "text", "content": "Payment failed ❌ Please try again."},
                        WORKER_ID,
                        "stripe_failed"
                    )

                    continue
                reply_text = None
                reply_product_id = None
                callback = data.get("callback_query")

                if callback:
                    user_id = callback["from"]["id"]
                    user_text = callback.get("data")
                    chat_id = callback["message"]["chat"]["id"]
                else:
                    message = data.get("message") or {}
                    if not message:
                        continue

                    reply = message.get("reply_to_message")
                    if reply and "photo" in reply:
                        file_id = reply["photo"][-1]["file_id"]

                        product_id = r.get(f"img:{business_id}:{file_id}")
                        reply_product_id=product_id
                        print("🧠 User replied to product:", product_id)
                    reply_text = reply.get("text") if reply else None

                    user_id = message["from"]["id"]
                    user_text = (message.get("text") or "").strip()
                    chat_id = message["chat"]["id"]
                    if not message:
                        continue
                    user_id = message["from"]["id"]
                    user_text = (message.get("text") or "").strip()
                    chat_id = message["chat"]["id"]

                thread_id = str(user_id)
                turn_id = str(uuid.uuid4())
                print("Reply Text:",reply_text)
                response = await chat_turn(
                    thread_id=thread_id,
                    text=user_text,
                    business_id=business_id,
                    turn_id=turn_id,
                    reply_to_text=reply_text,
                    reply_to_product=reply_product_id
                )

                print(f"[{WORKER_ID}] agent_response={response}")

                envelope = json.loads(response)

                # 1️⃣ SEND INITIAL MESSAGE FIRST
                messages = format_for_telegram(envelope)

                payment_message_ids = []

                for msg in messages:
                    res = await send_telegram_message(
                        chat_id=chat_id,
                        msg=msg,
                        worker_id=WORKER_ID,
                        trace_id=str(data.get("update_id"))
                    )
                    
                    if msg["type"] == "photo" and msg.get("meta", {}).get("product_id") and res:
                        file_id = res  # ✅ now real Telegram file_id
                        product_id = msg["meta"]["product_id"]

                        r.setex(f"img:{business_id}:{file_id}", 7200, product_id)

                        print("STORING:", file_id, product_id)

                    if msg.get("meta", {}).get("payment_ui") and res:
                        payment_message_ids.append(res)

                # 2️⃣ THEN WAIT FOR PAYMENT (if required)
                if (
                    envelope.get("type") == "payments"
                    and envelope.get("data")
                    and envelope["data"].get("awaiting_payment")
                ):

                    reference = envelope["data"]["reference"]
                    amount_ui = envelope["data"]["amount"]
                    payment_id = envelope["data"]["payment_id"]

                    sig = await wait_for_eurc_payment(
                        reference=reference,
                        amount_ui=amount_ui,
                        timeout_seconds=300,
                    )

                    if sig:
                        payment_id = ObjectId(payment_id)
                        await update_payment_attempt(
                            payment_id,
                            business_id,
                            {
                                "stage": "completed",
                                "tx_signature": sig,
                                "paid_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        explorer_url = f"https://explorer.solana.com/tx/{sig}?cluster=devnet"
                        await send_telegram_message(
                            chat_id,
                            {"type": "text", "content": f"Payment was successful ✅\n\n[View on Solana Explorer]({explorer_url})"},
                            WORKER_ID,
                            str(data.get("update_id"))
                        )
                        
                        payment_doc = await payments(business_id).find_one({"_id": payment_id})

                        await create_order({
                            "business_id": business_id,
                            "thread_id": thread_id,
                            "payment_id": payment_id,
                            "cart_snapshot": payment_doc.get("cart_snapshot"),
                            "email": payment_doc.get("email"),
                            "address": payment_doc.get("address"),
                            "country": payment_doc.get("country"),
                            "amount": payment_doc.get("amount"),
                            "currency": payment_doc.get("currency"),
                        })
                        
                        cart = await load_cart(thread_id, business_id)
                        clear_cart(cart)
                        await save_cart(cart)

                    else:
                        payment_id = ObjectId(payment_id)

                        await update_payment_attempt(
                            payment_id,
                            business_id,
                            {"stage": "expired"},
                        )

                        # 🔥 DELETE payment UI messages
                        async with httpx.AsyncClient(timeout=15) as client:
                            for mid in payment_message_ids:
                                await client.post(
                                    f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                                    json={"chat_id": chat_id, "message_id": mid}
                                )

                        await send_telegram_message(
                            chat_id,
                            {"type": "text", "content": "Payment window expired. Please try again."},
                            WORKER_ID,
                            str(data.get("update_id"))
                        )
            except Exception as e:
                print(f"[{WORKER_ID}] ERROR: {e}")
                traceback.print_exc()

    finally:
        close_mongo()


if __name__ == "__main__":
    asyncio.run(main())