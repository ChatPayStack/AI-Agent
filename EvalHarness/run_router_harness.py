#!/usr/bin/env python3
"""
ChatPay Router Accuracy Harness — EvalHarness/run_router_harness.py
Runs router_golden.py queries through route_intent and reports raw accuracy,
final accuracy, and fell-back count. Writes per-query CSV to
EvalHarness/router_results_<timestamp>.csv.

Usage:
    python EvalHarness/run_router_harness.py
"""

import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# ── paths ─────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent      # EvalHarness/
ROOT = HERE.parent                          # project root

sys.path.insert(0, str(ROOT))   # for shopping_agent, db, etc.
sys.path.insert(0, str(HERE))   # for router_golden

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# ── business_id — same source as run_harness.py ────────────────────────────────
import json as _json
with open(HERE / "products-for-eval.json") as _f:
    _catalog: List[Dict] = _json.load(_f)
_catalog_bids = list({p["business_id"] for p in _catalog if p.get("business_id")})
if len(_catalog_bids) == 1:
    BUSINESS_ID: str = _catalog_bids[0]
elif len(_catalog_bids) > 1:
    raise RuntimeError(f"products-for-eval.json contains multiple business_ids: {_catalog_bids}")
else:
    BUSINESS_ID = "a72d085a-c944-4eeb-ad8f-8f9a7e864509"  # fallback

# ── project imports ────────────────────────────────────────────────────────────
from db import connect_mongo, close_mongo
from langchain_core.messages import HumanMessage
import shopping_agent                           # imported as module for monkeypatching
from shopping_agent import route_intent, State

# ── golden set ─────────────────────────────────────────────────────────────────
from router_golden import router_golden as golden_set

# ── valid labels (mirrors shopping_agent.py:168) ──────────────────────────────
ALLOWED = {"enquiry", "cart_add", "cart_remove", "cart_view", "cart_clear", "payments", "chitchat"}


def _validate_golden_set() -> List[str]:
    """Flag any expected labels that the router will never emit."""
    warnings = []
    for query, expected in golden_set:
        if expected not in ALLOWED:
            warnings.append(f"  UNKNOWN LABEL '{expected}' for query '{query}'")
    return warnings


# ── main eval loop ─────────────────────────────────────────────────────────────

async def run_eval() -> None:
    connect_mongo()
    try:
        print(f"\nChatPay Router Accuracy Harness")
        print(f"business_id : {BUSINESS_ID}")
        print(f"queries     : {len(golden_set)}\n")

        warnings = _validate_golden_set()
        if warnings:
            print("WARNING — golden set contains labels the router never emits:")
            for w in warnings:
                print(w)
            print()

        rows: List[Dict] = []

        for i, (query, expected) in enumerate(golden_set):
            # ── capture raw_label by intercepting log_event ────────────────────
            captured: Dict[str, Any] = {}
            _real_log_event = shopping_agent.log_event

            def _capture(event: Dict[str, Any], _store=captured, _real=_real_log_event):
                if event.get("event") == "router_intent":
                    _store.update(event)
                _real(event)   # forward — don't suppress existing instrumentation

            shopping_agent.log_event = _capture
            # also patch the reference inside llm_wrapper (same process, different ref)
            import llm_wrapper as _llm_wrapper
            _real_llm_log = _llm_wrapper.log_event
            _llm_wrapper.log_event = _capture

            try:
                state: State = {
                    "messages": [HumanMessage(content=query)],
                    "business_id": BUSINESS_ID,
                    "thread_id": "eval-router",
                    "turn_id": f"eval-{i}",
                }

                final_label = await route_intent(state)
                raw_label   = captured.get("raw_label", final_label)
                fell_back   = captured.get("fell_back", False)

                raw_correct   = "Y" if raw_label   == expected else "N"
                final_correct = "Y" if final_label == expected else "N"
                error = False

            except Exception as exc:
                raw_label     = "ERROR"
                final_label   = "ERROR"
                fell_back     = False
                raw_correct   = "N"
                final_correct = "N"
                error = True
                print(f"  ERROR on query '{query}': {exc}")

            finally:
                # always restore — even if an exception was raised mid-call
                shopping_agent.log_event   = _real_log_event
                _llm_wrapper.log_event     = _real_llm_log

            rows.append({
                "query":         query,
                "expected":      expected,
                "raw_label":     raw_label,
                "final_label":   final_label,
                "raw_correct":   raw_correct,
                "final_correct": final_correct,
                "fell_back":     "Y" if fell_back else "N",
                "error":         error,
            })

        # ── aggregate metrics ──────────────────────────────────────────────────
        total       = len(rows)
        error_count = sum(1 for r in rows if r["error"])
        evaluated   = [r for r in rows if not r["error"]]

        raw_hits   = sum(1 for r in evaluated if r["raw_correct"]   == "Y")
        final_hits = sum(1 for r in evaluated if r["final_correct"] == "Y")
        fell_back_count = sum(1 for r in evaluated if r["fell_back"] == "Y")

        raw_acc   = raw_hits   / len(evaluated) if evaluated else 0.0
        final_acc = final_hits / len(evaluated) if evaluated else 0.0

        # ── console table ──────────────────────────────────────────────────────
        COL = [38, 12, 12, 12, 12, 14]
        HDR = ["query", "expected", "raw", "final", "raw_correct", "final_correct"]
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
            print(_line([
                r["query"], r["expected"], r["raw_label"],
                r["final_label"], r["raw_correct"], r["final_correct"],
            ]))
        print(SEP)

        print()
        print(f"  Total queries  : {total}")
        print(f"  Errors         : {error_count}")
        print(f"  Raw accuracy   : {raw_acc:.0%}  ({raw_hits}/{len(evaluated)})")
        print(f"  Final accuracy : {final_acc:.0%}  ({final_hits}/{len(evaluated)})")
        print(f"  Fell back      : {fell_back_count}  (raw label not in allowed set → coerced to chitchat)")
        print()

        # ── CSV ────────────────────────────────────────────────────────────────
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = HERE / f"router_results_{ts}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as cf:
            writer = csv.DictWriter(cf, fieldnames=[
                "query", "expected", "raw_label", "final_label",
                "raw_correct", "final_correct", "fell_back",
            ])
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r[k] for k in writer.fieldnames})

        print(f"  CSV → {csv_path.relative_to(ROOT)}")
        print()

    finally:
        close_mongo()


if __name__ == "__main__":
    asyncio.run(run_eval())
