import os
import math
from typing import List, Dict, Any
from pymongo import MongoClient
from openai import OpenAI
from dotenv import load_dotenv
import re

load_dotenv()
# --- setup ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
mongo = MongoClient(os.getenv("MONGODB_URI"))
db = mongo[os.getenv("MONGODB_DB", "test")]  # change if needed


# --- utils ---
def _embed_model():
    return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def normalize(text: str):
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return text.strip()


def extract_collection_filter(question: str, db, business_id: str):
    q = normalize(question)

    categories = list(db["categories"].find({"business_id": business_id}))

    best_match = None
    best_score = 0

    for c in categories:
        title = c.get("title") or ""
        norm_title = normalize(title)

        # remove generic words
        title_tokens = set(norm_title.replace("collection", "").split())
        q_tokens = set(q.split())

        # 🔑 also allow numeric match
        overlap = len(q_tokens & title_tokens)

        # handle number ↔ word equivalence
        number_map = {
            "1": "one",
            "2": "two",
            "3": "three",
        }

        for q_token in q_tokens:
            if q_token in number_map and number_map[q_token] in title_tokens:
                overlap += 1

        # 🔑 extra: match numbers explicitly
        for token in title_tokens:
            if token.isdigit() and token in q_tokens:
                overlap += 1

        if overlap > best_score:
            best_score = overlap
            best_match = title

    if best_score > 0:
        return best_match

    return None

# --- main test function ---
def rag_search_test(business_id: str, question: str, top_k: int = 20):
    print(f"\n🔍 QUESTION: {question}")

    # 1️⃣ detect filter
    collection_filter = extract_collection_filter(question, db, business_id)
    print("Detected filter:", collection_filter)

    query_filter = {"business_id": business_id}
    if collection_filter:
        query_filter["category"] = collection_filter

    # 2️⃣ embed query
    q_emb = client.embeddings.create(
        model=_embed_model(),
        input=[question],
    ).data[0].embedding

    # 3️⃣ fetch filtered vectors
    projection = {
        "embedding": 1,
        "name": 1,
        "price": 1,
        "category": 1,
        "description": 1,
    }

    vecs = list(db["vectors"].find(query_filter, projection))

    print(f"Fetched {len(vecs)} vectors after filtering")

    # 4️⃣ score
    scored = []
    for v in vecs:
        emb = v.get("embedding")
        if not emb:
            continue
        score = _cosine(q_emb, emb)
        scored.append((score, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    # 5️⃣ print results
    print("\n✅ Top Results:\n")
    for s, v in top:
        print(f"{round(s,3)} | {v.get('name')} | {v.get('category')}")

    return top


# --- run test ---
if __name__ == "__main__":
    

    rag_search_test('1c1d2f01-c889-4021-828e-688b482f1c2d', 'all products in collection 1')