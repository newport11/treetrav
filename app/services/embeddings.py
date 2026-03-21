"""Embedding generation and vector similarity search."""
import json
import math

import requests
from sklearn.feature_extraction.text import TfidfVectorizer

from app import db
from app.models import CanonicalUrl, Post, UrlEmbedding, UrlMetadata


EMBEDDING_API = "https://api.openai.com/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-3-small"

# In-memory TF-IDF vectorizer — rebuilt on first search
_tfidf_vectorizer = None
_tfidf_texts = None
_tfidf_ids = None


def generate_embedding(text, api_key):
    """Generate an embedding vector for the given text using OpenAI API."""
    response = requests.post(
        EMBEDDING_API,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": EMBEDDING_MODEL,
            "input": text[:8000],
        },
        timeout=30,
    )
    if response.status_code == 200:
        return response.json()["data"][0]["embedding"]
    return None


def generate_embeddings_batch(texts, api_key):
    """Generate embeddings for a batch of texts (max 2048 per call)."""
    response = requests.post(
        EMBEDDING_API,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": EMBEDDING_MODEL,
            "input": [t[:8000] for t in texts],
        },
        timeout=60,
    )
    if response.status_code == 200:
        data = response.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]
    return None


def build_text_for_url(canonical_url_id):
    """Build a rich text representation of a URL for embedding."""
    cu = CanonicalUrl.query.get(canonical_url_id)
    if not cu:
        return None

    parts = [cu.canonical_url, cu.domain or ""]

    posts = Post.query.filter_by(canonical_url_id=canonical_url_id).limit(5).all()
    for p in posts:
        if p.body:
            parts.append(p.body)
        if p.description:
            parts.append(p.description)

    metas = UrlMetadata.query.filter_by(canonical_url_id=canonical_url_id).limit(3).all()
    for m in metas:
        if m.summary:
            parts.append(m.summary)
        if m.relevance_justification:
            parts.append(m.relevance_justification)
        if m.entities:
            ents = m.entities if isinstance(m.entities, list) else json.loads(m.entities) if m.entities else []
            if ents:
                parts.append(", ".join(str(e) for e in ents))

    from app.models import PostTopicTag, Topic
    topic_ids = (
        db.session.query(PostTopicTag.topic_id)
        .filter(PostTopicTag.post_id.in_([p.id for p in posts]))
        .distinct()
        .all()
    )
    if topic_ids:
        topics = Topic.query.filter(Topic.id.in_([t[0] for t in topic_ids])).all()
        parts.append(" ".join(t.name for t in topics))

    text = " | ".join(p for p in parts if p)
    return text[:8000]


def cosine_similarity(a, b):
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_tfidf_vectorizer():
    """Get or rebuild the TF-IDF vectorizer from stored embeddings."""
    global _tfidf_vectorizer, _tfidf_texts, _tfidf_ids

    if _tfidf_vectorizer is not None:
        return _tfidf_vectorizer, _tfidf_texts, _tfidf_ids

    embeddings = UrlEmbedding.query.all()
    if not embeddings:
        return None, None, None

    _tfidf_texts = [e.text_content or "" for e in embeddings]
    _tfidf_ids = [e.canonical_url_id for e in embeddings]

    _tfidf_vectorizer = TfidfVectorizer(max_features=512, stop_words="english", sublinear_tf=True)
    _tfidf_vectorizer.fit(_tfidf_texts)

    return _tfidf_vectorizer, _tfidf_texts, _tfidf_ids


def semantic_search(query, api_key=None, limit=10, min_score=0.0):
    """Search for URLs by semantic similarity to the query.

    Uses stored TF-IDF vectors for local search (no API call needed for query).
    If api_key is provided and embeddings are OpenAI-based, uses OpenAI for query embedding.

    Returns list of (canonical_url_id, similarity_score) sorted by score desc.
    """
    embeddings = UrlEmbedding.query.all()
    if not embeddings:
        return []

    first_emb = embeddings[0]

    # Check if embeddings are TF-IDF based (local) or OpenAI based
    if first_emb.model and "tfidf" in first_emb.model:
        return _tfidf_search(query, embeddings, limit, min_score)
    elif api_key:
        return _openai_search(query, api_key, embeddings, limit, min_score)
    else:
        # No API key and non-TF-IDF embeddings — can't search
        return []


def _tfidf_search(query, embeddings, limit, min_score):
    """Search using TF-IDF vectorizer — fully local, no API needed."""
    vectorizer, texts, ids = _get_tfidf_vectorizer()
    if vectorizer is None:
        return []

    # Transform query using the fitted vectorizer
    query_vec = vectorizer.transform([query]).toarray()[0].tolist()

    results = []
    for emb in embeddings:
        vec = emb.get_vector()
        if not vec:
            continue
        sim = cosine_similarity(query_vec, vec)
        if sim >= min_score:
            results.append((emb.canonical_url_id, sim))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]


def _openai_search(query, api_key, embeddings, limit, min_score):
    """Search using OpenAI embeddings — needs API call for query."""
    query_vector = generate_embedding(query, api_key)
    if not query_vector:
        return []

    results = []
    for emb in embeddings:
        vec = emb.get_vector()
        if not vec:
            continue
        sim = cosine_similarity(query_vector, vec)
        if sim >= min_score:
            results.append((emb.canonical_url_id, sim))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]
