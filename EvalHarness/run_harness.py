#!/usr/bin/env python3
"""
ChatPay RAG Eval Harness
Runs golden_file.py queries through rag_search and reports recall@1, recall@3,
and NONE precision. Writes a per-query CSV to EvalHarness/results_<timestamp>.csv.

Usage:
    python EvalHarness/run_harness.py
"""

import asyncio
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── paths ─────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent          # EvalHarness/
ROOT = HERE.parent                              # project root

sys.path.insert(0, str(ROOT))   # for db, rag_query, etc.
sys.path.insert(0, str(HERE))   # for golden_file

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from db import connect_mongo, close_mongo
from rag_query import rag_search
from golden_file import golden_set

# ── config ────────────────────────────────────────────────────────────────────
# Business ID comes from products-for-eval.json — that file is the source of
# truth for which merchant we're evaluating. The worker BUSINESS_ID env var
# is intentionally ignored here; it belongs to whichever merchant the worker
# process handles and is unrelated to the eval catalog.
_FALLBACK_BID = "a72d085a-c944-4eeb-ad8f-8f9a7e864509"

TOP_K = 5
# Threshold rag_search uses to decide a confident match (rag_query.py:195)
NONE_THRESHOLD = 0.45

# ── catalog ───────────────────────────────────────────────────────────────────
with open(HERE / "products-for-eval.json") as _f:
    _catalog: List[Dict] = json.load(_f)

# Derive business_id from the catalog: all entries share the same merchant.
_catalog_bids = list({p["business_id"] for p in _catalog if p.get("business_id")})
if len(_catalog_bids) == 1:
    BUSINESS_ID: str = _catalog_bids[0]
elif len(_catalog_bids) > 1:
    raise RuntimeError(f"products-for-eval.json contains multiple business_ids: {_catalog_bids}")
else:
    BUSINESS_ID = _FALLBACK_BID

_name_to_handle: Dict[str, str] = {
    p["name"].lower(): p["handle"] for p in _catalog
}
_valid_handles: set = {p["handle"] for p in _catalog}


def _resolve_handle(name: Optional[str]) -> Optional[str]:
    """Map a product name from rag_search back to its catalog handle."""
    if not name:
        return None
    return _name_to_handle.get(name.lower())


# ── golden-set validation ─────────────────────────────────────────────────────

def _validate_golden_set() -> List[str]:
    """Return a list of warning strings for IDs that don't exist in the catalog."""
    warnings = []
    for query, expected in golden_set:
        if isinstance(expected, str) and expected.upper() in ("ALL", "NONE"):
            continue
        ids = expected if isinstance(expected, list) else [expected]
        for pid in ids:
            if pid not in _valid_handles:
                warnings.append(f"  UNKNOWN ID '{pid}' for query '{query}'")
    return warnings


# ── hit logic ─────────────────────────────────────────────────────────────────

def _is_hit(
    returned_ids: List[str],
    expected,
    at_k: int,
    result: Dict[str, Any],
) -> bool:
    """
    True if the query counts as a hit at rank k.

    expected variants:
      str  "ALL"  / "all"  — any result returned counts
      str  "NONE" / "none" — hit = agent has no confident match (best_product is None)
      str  "<handle>"      — exact handle must appear in top-k
      list ["<h1>","<h2>"] — any handle in the list must appear in top-k
    """
    top = returned_ids[:at_k]
    exp_upper = expected.upper() if isinstance(expected, str) else None

    if exp_upper == "ALL":
        # Discovery query — we just need something to have been returned
        has_products = len(result.get("product_matches", [])) > 0
        has_info = len(result.get("info_matches", [])) > 0
        return has_products or has_info

    if exp_upper == "NONE":
        # Out-of-catalog query — hit when the agent would NOT surface a product.
        # rag_search only sets best_product when score >= 0.45 with sufficient gap
        # (rag_query.py:195). A None best_product means the agent stays silent.
        return result.get("best_product") is None

    if isinstance(expected, list):
        return any(e in top for e in expected)

    return expected in top


# ── main eval loop ─────────────────────────────────────────────────────────────

async def run_eval() -> None:
    connect_mongo()
    try:
        print(f"\nChatPay RAG Eval Harness")
        print(f"business_id : {BUSINESS_ID}")
        print(f"top_k       : {TOP_K}")
        print(f"queries     : {len(golden_set)}\n")

        # Validate golden set IDs against catalog before running
        warnings = _validate_golden_set()
        if warnings:
            print("WARNING — golden set contains IDs not found in products-for-eval.json.")
            print("These queries will always report a miss even if retrieval works:\n")
            for w in warnings:
                print(w)
            print()

        rows: List[Dict] = []

        for query, expected in golden_set:
            exp_str = json.dumps(expected) if isinstance(expected, list) else str(expected)
            try:
                result = await rag_search(
                    business_id=BUSINESS_ID,
                    question=query,
                    top_k=TOP_K,
                )

                matches = result.get("product_matches", [])
                # Build deduplicated ordered list of handles from returned names
                seen: set = set()
                returned_ids: List[str] = []
                for m in matches[:TOP_K]:
                    handle = _resolve_handle(m.get("name"))
                    if handle and handle not in seen:
                        seen.add(handle)
                        returned_ids.append(handle)
                    elif not handle and m.get("chunk_id"):
                        # Fallback: use raw chunk_id if name lookup fails
                        cid = str(m["chunk_id"])
                        if cid not in seen:
                            seen.add(cid)
                            returned_ids.append(cid)

                hit1 = _is_hit(returned_ids, expected, 1, result)
                hit3 = _is_hit(returned_ids, expected, 3, result)
                top3_str = ", ".join(returned_ids[:3]) if returned_ids else "(none)"
                error = False

            except Exception as exc:
                returned_ids = []
                hit1 = False
                hit3 = False
                top3_str = "ERROR"
                error = True
                result = {}
                print(f"  ERROR on query '{query}': {exc}")

            rows.append({
                "query": query,
                "expected": exp_str,
                "top3_returned": top3_str,
                "hit@1": "Y" if hit1 else "N",
                "hit@3": "Y" if hit3 else "N",
                "error": error,
                "is_none": isinstance(expected, str) and expected.upper() == "NONE",
                "is_all": isinstance(expected, str) and expected.upper() == "ALL",
            })

        # ── aggregate metrics ──────────────────────────────────────────────────
        total = len(rows)
        error_count = sum(1 for r in rows if r["error"])
        evaluated = [r for r in rows if not r["error"]]

        # Recall = non-NONE queries (NONE is a separate failure mode)
        recall_rows = [r for r in evaluated if not r["is_none"]]
        none_rows   = [r for r in evaluated if r["is_none"]]

        r1_hits = sum(1 for r in recall_rows if r["hit@1"] == "Y")
        r3_hits = sum(1 for r in recall_rows if r["hit@3"] == "Y")
        recall_1 = r1_hits / len(recall_rows) if recall_rows else 0.0
        recall_3 = r3_hits / len(recall_rows) if recall_rows else 0.0

        none_hits = sum(1 for r in none_rows if r["hit@1"] == "Y")
        none_prec = none_hits / len(none_rows) if none_rows else 0.0

        # ── console table ──────────────────────────────────────────────────────
        COL = [36, 32, 42, 6, 6]
        HDR = ["query", "expected", "top-3 returned", "hit@1", "hit@3"]
        SEP = "+" + "+".join("-" * (w + 2) for w in COL) + "+"

        def _cell(val: str, width: int) -> str:
            v = str(val)
            if len(v) > width:
                v = v[:width - 1] + "…"
            return f" {v:<{width}} "

        def _line(vals):
            return "|" + "|".join(_cell(v, w) for v, w in zip(vals, COL)) + "|"

        print(SEP)
        print(_line(HDR))
        print(SEP)
        for r in rows:
            print(_line([r["query"], r["expected"], r["top3_returned"],
                         r["hit@1"], r["hit@3"]]))
        print(SEP)

        print()
        print(f"  Total queries  : {total}")
        print(f"  Errors         : {error_count}")
        print(f"  Recall@1       : {recall_1:.0%}  ({r1_hits}/{len(recall_rows)} non-NONE queries)")
        print(f"  Recall@3       : {recall_3:.0%}  ({r3_hits}/{len(recall_rows)} non-NONE queries)")
        print(f"  NONE precision : {none_prec:.0%}  ({none_hits}/{len(none_rows)} NONE queries correctly rejected)")
        print()

        # ── CSV ────────────────────────────────────────────────────────────────
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = HERE / f"results_{ts}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as cf:
            writer = csv.DictWriter(
                cf,
                fieldnames=["query", "expected", "top3_returned", "hit@1", "hit@3"],
            )
            writer.writeheader()
            for r in rows:
                writer.writerow({
                    "query":        r["query"],
                    "expected":     r["expected"],
                    "top3_returned": r["top3_returned"],
                    "hit@1":        r["hit@1"],
                    "hit@3":        r["hit@3"],
                })

        print(f"  CSV → {csv_path.relative_to(ROOT)}")
        print()

    finally:
        close_mongo()


if __name__ == "__main__":
    asyncio.run(run_eval())
