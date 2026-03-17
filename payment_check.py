# payment_check.py
import asyncio
import os
import time
from decimal import Decimal
from typing import Optional

import httpx
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from spl.token.instructions import get_associated_token_address

from solders.signature import Signature

import json


def _to_eurc_base_units(amount_ui: str) -> int:
    # EURC also uses 6 decimals
    return int((Decimal(amount_ui) * Decimal(10**6)).to_integral_value())


def _tx_pays_eurc_to_business(tx_json: dict, *, business_ata: str, eurc_mint: str, amount_base: int) -> bool:
    res = (tx_json or {}).get("result") or {}
    meta = res.get("meta") or {}
    if meta.get("err") is not None:
        return False

    pre = meta.get("preTokenBalances") or []
    post = meta.get("postTokenBalances") or []

    msg = (res.get("transaction") or {}).get("message") or {}
    account_keys = msg.get("accountKeys") or []

    pre_map = {(tb.get("accountIndex"), tb.get("mint")): int(tb.get("amount") or 0) for tb in pre}
    post_map = {(tb.get("accountIndex"), tb.get("mint")): int(tb.get("amount") or 0) for tb in post}

    for (acct_idx, mint), post_amt in post_map.items():
        if mint != eurc_mint:
            continue
        if acct_idx is None or acct_idx >= len(account_keys):
            continue
        if account_keys[acct_idx] != business_ata:
            continue

        pre_amt = pre_map.get((acct_idx, mint), 0)
        if post_amt - pre_amt == amount_base:
            return True

    return False


async def wait_for_eurc_payment(
    *,
    reference: str,
    amount_ui: str,
    timeout_seconds: int = 300,
    poll_every_seconds: int = 5,
) -> Optional[str]:
    rpc_url = os.getenv("MAINNET_RPC_URL", "https://api.mainnet-beta.solana.com")
    business_address = os.getenv("BUSINESS_ADDRESS")
    eurc_mint = os.getenv("EURC_MINT_MAINNET")

    if not business_address:
        raise RuntimeError("BUSINESS_ADDRESS missing in .env")
    if not eurc_mint:
        raise RuntimeError("EURC_MINT_MAINNET missing in .env")

    ref_pk = Pubkey.from_string(reference)
    business_pk = Pubkey.from_string(business_address)
    eurc_mint_pk = Pubkey.from_string(eurc_mint)

    business_ata = str(get_associated_token_address(owner=business_pk, mint=eurc_mint_pk))
    amount_base = _to_eurc_base_units(amount_ui)

    deadline = time.monotonic() + timeout_seconds
    seen: set[str] = set()

    async with AsyncClient(rpc_url) as client:
        while time.monotonic() < deadline:
            try:
                sigs_resp = await client.get_signatures_for_address(ref_pk, limit=10, commitment=Confirmed)
            except Exception as e:
                cause = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
                if isinstance(cause, httpx.HTTPStatusError):
                    code = cause.response.status_code
                    if code == 429:
                        await asyncio.sleep(max(10, poll_every_seconds))
                        continue
                raise

            for s in (sigs_resp.value or []):
                sig = str(s.signature)
                if sig in seen:
                    continue
                seen.add(sig)

                tx_resp = await client.get_transaction(
                    Signature.from_string(sig),
                    encoding="jsonParsed",
                    commitment=Confirmed,
                    max_supported_transaction_version=0,
                )
                tx_json = json.loads(tx_resp.to_json())

                if _tx_pays_eurc_to_business(
                    tx_json,
                    business_ata=business_ata,
                    eurc_mint=eurc_mint,
                    amount_base=amount_base,
                ):
                    return sig

            await asyncio.sleep(poll_every_seconds)

    return None