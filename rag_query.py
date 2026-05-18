import os
import math
from typing import List, Dict, Any

from openai import OpenAI
from db import get_db

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from llm_wrapper import call_llm

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _embed_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


def _cosine(a: List[float], b: List[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return dot / denom if denom else 0.0


def _fmt_last_messages(last_messages: List[Dict[str, str]], limit: int = 10) -> str:
    msgs = last_messages[-limit:] if limit else last_messages
    lines = []
    for m in msgs:
        role = (m.get("role") or "user").strip().lower()
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


def classify_intent(question: str) -> str:
    q = question.lower()

    discovery_keywords = [
        "show me",
        "products",
        "catalog",
        "collection",
        "pictures",
        "photos",
        "what do you sell",
        "browse",
        "all products",
    ]

    for kw in discovery_keywords:
        if kw in q:
            return "discovery"

    return "specific"

async def rag_search(
    *,
    business_id: str,
    question: str,
    top_k: int = 6,
) -> Dict[str, Any]:
    db = get_db()

    # 🔥 STEP 1 — intent classification (multilingual)
    intent =  classify_intent(question)

    # =====================================================
    # 🔥 DISCOVERY MODE → bypass embeddings
    # =====================================================
    if intent == "discovery":
        cursor = db["vectors"].find(
            {"business_id": business_id, "type": "product"},
            {
                "name": 1,
                "price": 1,
                "description": 1,
                "asset_ids": 1,
                "chunk_id": 1,
                "product_index": 1,
            }
        )

        products = await cursor.to_list(length=5)

        product_matches = [
            {
                "type": "product",
                "score": 1.0,
                "product_index": p.get("product_index"),
                "name": p.get("name"),
                "price": p.get("price"),
                "description": p.get("description"),
                "asset_ids": p.get("asset_ids", []),
                "chunk_id": p.get("chunk_id"),
            }
            for p in products
        ]

        return {
            "question": question,
            "best_product": None,
            "product_matches": product_matches,
            "info_matches": [],
        }

    # =====================================================
    # 🔥 SPECIFIC MODE → normal semantic search
    # =====================================================

    q_emb = client.embeddings.create(
        model=_embed_model(),
        input=[question],
    ).data[0].embedding

    projection = {
        "embedding": 1,
        "type": 1,
        "kind": 1,
        "chunk_id": 1,
        "file_id": 1,
        "source_type": 1,
        "chunk_index": 1,
        "title": 1,
        "text": 1,
        "name": 1,
        "price": 1,
        "description": 1,
        "asset_ids": 1,
        "product_index": 1,
    }

    cursor = db["vectors"].find({"business_id": business_id}, projection)
    vecs = await cursor.to_list(length=20_000)

    scored = []
    for v in vecs:
        emb = v.get("embedding")
        if not emb:
            continue
        score = _cosine(q_emb, emb)
        scored.append((score, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: max(1, min(top_k, 50))]

    product_matches = []
    info_matches = []

    for score, v in top:
        vtype = v.get("type") or ("product" if v.get("kind") == "product" else "info")

        if vtype == "product":
            product_matches.append(
                {
                    "type": "product",
                    "score": score,
                    "product_index": v.get("product_index"),
                    "name": v.get("name"),
                    "price": v.get("price"),
                    "description": v.get("description"),
                    "asset_ids": v.get("asset_ids", []),
                    "chunk_id": v.get("chunk_id"),
                }
            )
        else:
            info_matches.append(
                {
                    "type": "info",
                    "score": score,
                    "title": v.get("title") or "Info",
                    "description": v.get("text"),
                    "chunk_id": v.get("chunk_id"),
                    "file_id": v.get("file_id"),
                    "source_type": v.get("source_type"),
                    "chunk_index": v.get("chunk_index"),
                }
            )

    best_product = None
    if product_matches:
        top1 = product_matches[0]
        top2 = product_matches[1] if len(product_matches) > 1 else None

        top1_score = top1["score"]
        top2_score = top2["score"] if top2 else 0.0
        gap = top1_score - top2_score

        if top1_score >= 0.45 and (gap >= 0.05 or top1_score >= 0.60):
            best_product = top1

    return {
        "question": question,
        "best_product": best_product,
        "product_matches": product_matches,
        "info_matches": info_matches,
    }


# ================================
# rag_message (unchanged except safety)
# ================================
async def rag_message(
    *,
    business_id: str,
    question: str,
    last_messages: List[Dict[str, str]] | None = None,
    thread_id: str,
    turn_id: str,
    top_k: int = 6,
    model: str = "gpt-4.1-mini",
) -> Dict[str, Any]:

    search = await rag_search(business_id=business_id, question=question, top_k=top_k)

    product_matches = search.get("product_matches", [])
    info_matches = search.get("info_matches", [])

    ctx_parts = []

    for i, m in enumerate(product_matches, start=1):
        ctx_parts.append(
            f"[Product {i}] {m.get('name')} | {m.get('price')}\n{m.get('description')}"
        )

    for i, m in enumerate(info_matches, start=1):
        ctx_parts.append(
            f"[Info {i}] {m.get('title')}\n{m.get('description')}"
        )

    context = "\n\n".join(ctx_parts)
    convo = _fmt_last_messages(last_messages or [], limit=10)

    prompt = (
        "You are a skincare store assistant.\n"
        "IMPORTANT: Do NOT output URLs, links, or images.\n\n"
        f"CONVERSATION:\n{convo}\n\n"
        f"QUESTION:\n{question}\n\n"
        f"CONTEXT:\n{context}"
    )

    llm = ChatOpenAI(model=model, temperature=0)

    resp = await call_llm(
        llm,
        [HumanMessage(content=prompt)],
        business_id=business_id,
        thread_id=thread_id,
        turn_id=turn_id,
        agent_node="enquiry",
    )

    return {"message": resp.content}