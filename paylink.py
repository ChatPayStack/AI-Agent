# paylink.py
import urllib.parse as up
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()

EURC_MINT_MAINNET = os.getenv("EURC_MINT_MAINNET")

def solana_pay_url(
    recipient: str,
    amount: str,
    *,
    spl_token: Optional[str] = EURC_MINT_MAINNET,  # ✅ default → EURC
    label: str = "ChatPay",
    message: Optional[str] = None,
    memo: Optional[str] = None,
    reference: Optional[str] = None,
) -> str:
    if not recipient or not isinstance(recipient, str):
        raise ValueError("recipient must be a non-empty string")
    if not amount or not isinstance(amount, str):
        raise ValueError("amount must be a non-empty string (e.g., '5.25')")

    params = {"amount": amount}

    if spl_token:
        params["spl-token"] = spl_token
    if label:
        params["label"] = label
    if message:
        params["message"] = message
    if memo:
        params["memo"] = memo
    if reference:
        params["reference"] = reference

    return f"solana:{recipient}?{up.urlencode(params, quote_via=up.quote)}"