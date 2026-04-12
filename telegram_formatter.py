# telegram_formatter.py

from typing import Dict, Any, List
import re
from generatePayQR import generate_solana_qr
import io

CONFIDENCE_THRESHOLD = 0.8


def strip_html(text: str) -> str:
    return re.sub("<.*?>", "", text or "")


def format_for_telegram(envelope: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert agent envelope JSON into Telegram-friendly messages.

    Returns a list of actions:
    {
      "type": "text" | "photo",
      "content": str,
      "inline_buttons": optional
    }
    """
    out: List[Dict[str, Any]] = []
    etype = envelope.get("type")

    # -----------------
    # CHITCHAT
    # -----------------
    if etype == "chitchat":
        out.append({
            "type": "text",
            "content": envelope.get("message", "")
        })
        return out

    # -----------------
    # PAYMENTS (disabled)
    # -----------------
    if etype == "payments":
        data = envelope.get("data") or {}
        message = envelope.get("message") or ""

        # ✅ Only do QR flow if message actually contains a link (crypto)
        if data.get("awaiting_payment") and "\n" in message:
            parts = message.split("\n")

            # extra safety
            if len(parts) > 1:
                solana_link = parts[1]

                qr_img = generate_solana_qr(solana_link)

                buffer = io.BytesIO()
                qr_img.save(buffer, format="PNG")
                buffer.seek(0)

                out.append({
                    "type": "qr",
                    "content": buffer,
                    "meta": {"payment_ui": True}
                })

                out.append({
                    "type": "text",
                    "content": message,
                    "meta": {"payment_ui": True}
                })

                return out

        # ✅ Otherwise → normal (Stripe / buttons flow)
        reply_markup = None
        if data.get("inline_buttons"):
            reply_markup = {
                "inline_keyboard": data.get("inline_buttons")
            }

        out.append({
            "type": "text",
            "content": message,
            "reply_markup": reply_markup
        })

        return out
    # -----------------
    # CART (NO IMAGES)
    # -----------------
    if etype == "cart":
        cart = (envelope.get("data") or {}).get("cart") or {}
        items = cart.get("items") or []
        summary = cart.get("summary") or {}

        if not items:
            out.append({
                "type": "text",
                "content": "🛒 Your cart is empty."
            })
            return out

        lines = ["🛒 *Your cart*\n"]
        for it in items:
            name = it.get("name")
            qty = it.get("qty")
            price = it.get("unit_price_amount")
            currency = it.get("unit_price_currency", "")
            lines.append(f"• {name}\n  Qty: {qty} · {price} {currency}")

        lines.append(
            f"\nSubtotal: {summary.get('subtotal_amount')} {summary.get('currency')}"
        )

        out.append({
            "type": "text",
            "content": "\n".join(lines),
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "💳 Checkout", "callback_data": "checkout"}]
                ]
            }
        })
        return out

    # -----------------
    # ENQUIRY
    # -----------------
    if etype == "enquiry":
        data = envelope.get("data") or {}
        result_type = data.get("result_type")

        # ==========================
        # 🔥 SINGLE PRODUCT (CARD)
        # ==========================
        if result_type == "product" and data.get("product"):
            p = data["product"]

            # 🔥 1️⃣ Show agent message FIRST
            out.append({
                "type": "text",
                "content": envelope.get("message", "")
            })

            # 2️⃣ Send main image
            assets = p.get("suggested_asset_ids") or []
            if assets:
                out.append({
                    "type": "photo",
                    "content": assets[0],
                    "meta": { "product_id": p.get("name") }
                })

            # 3️⃣ Build card text
            clean_desc = strip_html(p.get("description", ""))[:400]

            card_text = (
                f"*{p.get('name')}*\n"
                f"€{p.get('price')}\n\n"
                f"{clean_desc}"
            )

            out.append({
                "type": "text",
                "content": card_text,
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "➕ Add to Cart", "callback_data": f"add:{p.get('name')}"}],
                        [{"text": "🛒 View Cart", "callback_data": "view_cart"}]
                    ]
                }
            })

            return out

        # ==========================
        # MULTIPLE PRODUCTS (UNCHANGED)
        # ==========================
        products = data.get("products") or []

        # 1️⃣ Images first
        for p in products:
            confidence = float(p.get("confidence") or 0.0)

            if confidence < CONFIDENCE_THRESHOLD:
                continue

            assets = p.get("suggested_asset_ids") or []
            if not assets:
                continue

            out.append({
                "type": "photo",
                "content": assets[0],
                "meta": {"product_id": p.get("name")}
            })
        # 2️⃣ Then message text
        out.append({
            "type": "text",
            "content": envelope.get("message", "")
        })

        return out

    # -----------------
    # FALLBACK
    # -----------------
    out.append({
        "type": "text",
        "content": envelope.get("message", "")
    })

    return out