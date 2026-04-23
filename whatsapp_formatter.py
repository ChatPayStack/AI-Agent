# whatsapp_formatter.py

from typing import Dict, Any, List
import re

CONFIDENCE_THRESHOLD = 0.5


def strip_html(text: str) -> str:
    return re.sub("<.*?>", "", text or "")


def format_for_whatsapp(envelope: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    # PAYMENTS (simplified)
    # -----------------
    if etype == "payments":
        out.append({
            "type": "text",
            "content": envelope.get("message", "")
        })
        return out

    # -----------------
    # CART
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

        lines = ["🛒 Your cart:\n"]

        for it in items:
            name = it.get("name")
            qty = it.get("qty")
            price = it.get("unit_price_amount")
            currency = it.get("unit_price_currency", "")
            lines.append(f"- {name} (x{qty}) — {price} {currency}")

        lines.append(
            f"\nSubtotal: {summary.get('subtotal_amount')} {summary.get('currency')}"
        )

        out.append({
            "type": "text",
            "content": "\n".join(lines)
        })

        return out

    # -----------------
    # ENQUIRY
    # -----------------
    if etype == "enquiry":
        data = envelope.get("data") or {}
        result_type = data.get("result_type")

        # ✅ SINGLE PRODUCT → IMAGE + TEXT
        if result_type == "product" and data.get("product"):
            p = data["product"]

            assets = p.get("suggested_asset_ids") or []
            if assets:
                out.append({
                    "type": "photo",
                    "content": assets[0]
                })

            clean_desc = strip_html(p.get("description", ""))[:400]

            text = (
                f"{p.get('name')}\n"
                f"€{p.get('price')}\n\n"
                f"{clean_desc}"
            )

            out.append({
                "type": "text",
                "content": text
            })

            return out

        # ✅ MULTIPLE PRODUCTS → TEXT ONLY (NO IMAGES)
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