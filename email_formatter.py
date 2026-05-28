# email_formatter.py

from typing import Dict, Any
import re


def strip_html(text: str) -> str:
    return re.sub("<.*?>", "", text or "")


def format_for_email(envelope: Dict[str, Any]) -> str:
    etype = envelope.get("type")

    if etype == "chitchat":
        return envelope.get("message", "")

    if etype == "payments":
        return envelope.get("message", "")

    if etype == "cart":
        cart = (envelope.get("data") or {}).get("cart") or {}
        items = cart.get("items") or []
        summary = cart.get("summary") or {}

        if not items:
            return "Your cart is currently empty."

        lines = ["Your cart:\n"]
        for it in items:
            name = it.get("name")
            qty = it.get("qty")
            price = it.get("unit_price_amount")
            currency = it.get("unit_price_currency", "")
            lines.append(f"  - {name} (x{qty}) — {price} {currency}")

        lines.append(
            f"\nSubtotal: {summary.get('subtotal_amount')} {summary.get('currency')}"
        )

        return "\n".join(lines)

    if etype == "enquiry":
        data = envelope.get("data") or {}
        result_type = data.get("result_type")

        if result_type == "product" and data.get("product"):
            p = data["product"]
            clean_desc = strip_html(p.get("description", ""))[:600]
            lines = [
                p.get("name", ""),
                f"Price: €{p.get('price')}",
                "",
                clean_desc,
            ]
            return "\n".join(lines)

        return envelope.get("message", "")

    return envelope.get("message", "")
