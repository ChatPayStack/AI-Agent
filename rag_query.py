import os
import math
from typing import List, Dict, Any

from openai import OpenAI
from db import get_db

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from llm_wrapper import call_llm
import re

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ----------------------------
# Utils
# ----------------------------

def _embed_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def normalize(text: str):
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def extract_collection_filter(question: str, categories):
    q_tokens = set(normalize(question).split())

    best_match = None
    best_score = 0

    for c in categories:
        title = c.get("title") or ""
        title_tokens = set(normalize(title).replace("collection", "").split())

        overlap = len(q_tokens & title_tokens)

        if overlap > best_score:
            best_score = overlap
            best_match = title

    return best_match if best_score > 0 else None


# ----------------------------
# RAG SEARCH
# ----------------------------

async def rag_search(
    *,
    business_id: str,
    question: str,
    top_k: int = 6,
) -> Dict[str, Any]:

    db = get_db()

    # 1. Embed
    q_emb = client.embeddings.create(
        model=_embed_model(),
        input=[question],
    ).data[0].embedding

    # 2. Categories
    categories = await db["categories"].find(
        {"business_id": business_id}
    ).to_list(length=50)

    collection_filter = extract_collection_filter(question, categories)
    is_collection_query = collection_filter is not None

    query_filter = {"business_id": business_id}
    if collection_filter:
        query_filter["category"] = collection_filter

    # 3. Fetch LIMITED vectors
    projection = {
        "embedding": 1,
        "type": 1,
        "name": 1,
        "price": 1,
        "description": 1,
        "asset_ids": 1,
        "chunk_id": 1,
        "file_id": 1,
        "title": 1,
        "text": 1,
    }

    vecs = await db["vectors"].find(query_filter, projection).to_list(length=200)

    # 4. Score
    scored = []
    for v in vecs:
        emb = v.get("embedding")
        if not emb:
            continue
        score = _cosine(q_emb, emb)
        scored.append((score, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: (top_k if not is_collection_query else 12)]

    product_matches = []
    info_matches = []

    for score, v in top:
        if v.get("type") == "product":
            product_matches.append({
                "score": score,
                "name": v.get("name"),
                "price": v.get("price"),
                "description": v.get("description"),
                "asset_ids": v.get("asset_ids", []),
                "chunk_id": v.get("chunk_id"),
            })
        else:
            info_matches.append({
                "score": score,
                "title": v.get("title"),
                "description": v.get("text"),
                "chunk_id": v.get("chunk_id"),
                "file_id": v.get("file_id"),
            })

    best_product = None

    if not is_collection_query:
        if len(product_matches) >= 1:
            top1 = product_matches[0]
            top2 = product_matches[1] if len(product_matches) > 1 else None

            if top1["score"] >= 0.30 and (not top2 or (top1["score"] - top2["score"]) >= 0.03):
                best_product = top1

    return {
        "best_product": best_product,
        "product_matches": product_matches,
        "info_matches": info_matches,
        "collection_filter": collection_filter,
    }


# ----------------------------
# RAG MESSAGE (FIXED)
# ----------------------------

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

    db = get_db()

    # ✅ ONLY ONE SEARCH (no duplicate anymore)
    search = await rag_search(
        business_id=business_id,
        question=question,
        top_k=top_k,
    )

    product_matches = search.get("product_matches", [])
    info_matches = search.get("info_matches", [])

    cfg = await db["agent_config"].find_one({"_id": business_id}) or {}
    tone = cfg.get("tone") or "helpful"

    ctx_parts = []

    for m in product_matches:
        ctx_parts.append(f"{m['name']} | {m['price']}\n{m['description']}")

    for m in info_matches:
        ctx_parts.append(f"{m['title']}\n{m['description']}")

    context = "\n\n".join(ctx_parts[:6])  # limit size

    convo = ""
    if last_messages:
        convo = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in last_messages[-6:]
        )

    prompt = f"""
    You are a store assistant. Tone: {tone}.

    Use ONLY this context.
    Be concise and helpful.

    CONVERSATION:
    {convo}

    QUESTION:
    {question}

    CONTEXT:
    {context}
    """

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