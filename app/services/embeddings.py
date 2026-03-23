"""
Semantic Search Engine for Treetrav
====================================

Two modes (auto-detected from config):

PRODUCTION (OPENAI_API_KEY set):
    - Embeddings: OpenAI text-embedding-3-small (1536 dims)
    - Search: FAISS HNSW index — sub-5ms at millions of vectors
    - Each URL embedded via API call at post time

DEVELOPMENT (no API key):
    - Embeddings: TF-IDF (512 dims, scikit-learn)
    - Search: FAISS flat index (exact search, still fast with FAISS)
    - Each URL embedded locally at post time

Architecture:
    1. URL posted → background thread generates embedding → stored in DB + appended to FAISS index
    2. Search query → vectorized same way → FAISS index.search() → top-k results in <5ms
    3. FAISS index rebuilt from DB on first search, then kept in memory
"""

import json
import threading

import faiss
import numpy as np
import requests
from sklearn.feature_extraction.text import TfidfVectorizer

from app import db
from app.models import CanonicalUrl, Post, UrlEmbedding, UrlMetadata


# ===========================================================================
# Config
# ===========================================================================

OPENAI_API = "https://api.openai.com/v1/embeddings"
OPENAI_MODEL = "text-embedding-3-small"
OPENAI_DIMS = 1536
TFIDF_DIMS = 512
MIN_SIMILARITY = 0.15

# ===========================================================================
# In-memory state
# ===========================================================================

_lock = threading.Lock()
_faiss_index = None  # faiss.Index
_id_map = []  # canonical_url_id at position i in the index
_id_set = set()
_dims = None  # dimensionality of vectors in the index

_tfidf_vectorizer = None
_tfidf_lock = threading.Lock()


# ===========================================================================
# Text building — what gets embedded for each URL
# ===========================================================================

def build_text_for_url(canonical_url_id):
    """Build rich text from all signals for a URL."""
    cu = CanonicalUrl.query.get(canonical_url_id)
    if not cu:
        return None

    parts = []
    if cu.domain:
        parts.append(cu.domain)

    posts = Post.query.filter_by(canonical_url_id=canonical_url_id).limit(5).all()
    for p in posts:
        if p.body:
            parts.append(p.body)
            parts.append(p.body)  # double-weight titles
        if p.description:
            parts.append(p.description)

    metas = UrlMetadata.query.filter_by(canonical_url_id=canonical_url_id).limit(3).all()
    for m in metas:
        if m.summary:
            parts.append(m.summary)
        if m.relevance_justification:
            parts.append(m.relevance_justification)
        if m.entities:
            ents = m.entities if isinstance(m.entities, list) else json.loads(m.entities) if isinstance(m.entities, str) and m.entities else []
            if ents:
                entity_str = ", ".join(str(e) for e in ents)
                parts.append(entity_str)
                parts.append(entity_str)  # double-weight entities

    from app.models import PostTopicTag, Topic
    if posts:
        topic_ids = (
            db.session.query(PostTopicTag.topic_id)
            .filter(PostTopicTag.post_id.in_([p.id for p in posts]))
            .distinct().all()
        )
        if topic_ids:
            topics = Topic.query.filter(Topic.id.in_([t[0] for t in topic_ids])).all()
            topic_str = " ".join(t.name for t in topics)
            parts.append(topic_str)
            parts.append(topic_str)  # double-weight topics

    text = " . ".join(p for p in parts if p)
    return text[:8000] if text else None


# ===========================================================================
# Embedding generation
# ===========================================================================

def _get_tfidf_vectorizer():
    """Get or build TF-IDF vectorizer for dev mode."""
    global _tfidf_vectorizer
    if _tfidf_vectorizer is not None:
        return _tfidf_vectorizer

    with _tfidf_lock:
        if _tfidf_vectorizer is not None:
            return _tfidf_vectorizer

        # Fit on existing text
        embeddings = UrlEmbedding.query.all()
        if embeddings:
            texts = [e.text_content or "" for e in embeddings if e.text_content]
        else:
            all_urls = CanonicalUrl.query.all()
            texts = [build_text_for_url(cu.id) or "" for cu in all_urls]

        texts = [t for t in texts if len(t) > 10]
        if not texts:
            return None

        _tfidf_vectorizer = TfidfVectorizer(
            max_features=TFIDF_DIMS,
            stop_words="english",
            sublinear_tf=True,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.95,
        )
        _tfidf_vectorizer.fit(texts)
        return _tfidf_vectorizer


def _embed_text_openai(text, api_key):
    """Generate OpenAI embedding."""
    try:
        r = requests.post(
            OPENAI_API,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": OPENAI_MODEL, "input": text[:8000]},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()["data"][0]["embedding"]
    except Exception:
        pass
    return None


def _embed_text_tfidf(text):
    """Generate TF-IDF embedding."""
    v = _get_tfidf_vectorizer()
    if v is None:
        return None
    return v.transform([text]).toarray()[0].tolist()


def _embed_text(text, api_key=None):
    """Generate embedding using best available method."""
    if api_key:
        vec = _embed_text_openai(text, api_key)
        if vec:
            return vec, "openai", OPENAI_DIMS
    # Fallback to TF-IDF
    vec = _embed_text_tfidf(text)
    if vec:
        return vec, "tfidf", TFIDF_DIMS
    return None, None, None


def generate_embeddings_batch(texts, api_key):
    """Batch OpenAI embeddings."""
    try:
        r = requests.post(
            OPENAI_API,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": OPENAI_MODEL, "input": [t[:8000] for t in texts]},
            timeout=60,
        )
        if r.status_code == 200:
            data = r.json()["data"]
            return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]
    except Exception:
        pass
    return None


# ===========================================================================
# FAISS Index
# ===========================================================================

def _build_faiss_index():
    """Build FAISS index from all stored embeddings."""
    global _faiss_index, _id_map, _id_set, _dims

    embeddings = UrlEmbedding.query.all()
    if not embeddings:
        _faiss_index = None
        _id_map = []
        _id_set = set()
        return

    vectors = []
    ids = []
    for emb in embeddings:
        vec = emb.get_vector()
        if vec:
            vectors.append(vec)
            ids.append(emb.canonical_url_id)

    if not vectors:
        return

    matrix = np.array(vectors, dtype=np.float32)
    _dims = matrix.shape[1]

    # Normalize for cosine similarity (inner product of normalized = cosine)
    faiss.normalize_L2(matrix)

    if len(vectors) < 1000:
        # Small dataset — use flat (exact) index
        _faiss_index = faiss.IndexFlatIP(_dims)
    else:
        # Larger — use HNSW for fast approximate search
        _faiss_index = faiss.IndexHNSWFlat(_dims, 32)  # 32 neighbors per node
        _faiss_index.hnsw.efSearch = 64  # search quality parameter
        _faiss_index.hnsw.efConstruction = 200  # build quality

    _faiss_index.add(matrix)
    _id_map = ids
    _id_set = set(ids)


def _get_faiss_index():
    """Get or build the FAISS index."""
    global _faiss_index
    if _faiss_index is not None:
        return _faiss_index

    with _lock:
        if _faiss_index is not None:
            return _faiss_index
        _build_faiss_index()
        return _faiss_index


def _append_to_index(canonical_url_id, vector):
    """Append a single vector to the FAISS index."""
    global _faiss_index, _id_map, _id_set, _dims

    if canonical_url_id in _id_set:
        return

    vec = np.array([vector], dtype=np.float32)
    faiss.normalize_L2(vec)

    with _lock:
        if _faiss_index is None:
            _dims = len(vector)
            _faiss_index = faiss.IndexFlatIP(_dims)

        if vec.shape[1] == _dims:
            _faiss_index.add(vec)
            _id_map.append(canonical_url_id)
            _id_set.add(canonical_url_id)


# ===========================================================================
# Core API
# ===========================================================================

def embed_url(canonical_url_id, api_key=None):
    """Generate and store embedding for a URL. Called after every post."""
    existing = UrlEmbedding.query.filter_by(canonical_url_id=canonical_url_id).first()
    if existing:
        return True

    text = build_text_for_url(canonical_url_id)
    if not text:
        return False

    vector, model, dims = _embed_text(text, api_key=api_key)
    if not vector:
        return False

    emb = UrlEmbedding(
        canonical_url_id=canonical_url_id,
        text_content=text[:2000],
        model=model,
        dimensions=dims,
    )
    emb.set_vector(vector)
    db.session.add(emb)
    db.session.commit()

    _append_to_index(canonical_url_id, vector)
    return True


def semantic_search(query, api_key=None, limit=10, min_score=MIN_SIMILARITY):
    """Search the content graph.

    1. Vectorize query (OpenAI if key available, else TF-IDF)
    2. FAISS index search — returns top-k nearest neighbors
    3. Filter by min_score threshold

    Returns list of (canonical_url_id, similarity_score).
    """
    index = _get_faiss_index()
    if index is None or index.ntotal == 0:
        return []

    # Vectorize query using same method as stored embeddings
    first_emb = UrlEmbedding.query.first()
    is_openai = first_emb and first_emb.model and "openai" in first_emb.model

    if is_openai and api_key:
        qvec = _embed_text_openai(query, api_key)
    else:
        qvec = _embed_text_tfidf(query)

    if not qvec:
        return []

    qvec = np.array([qvec], dtype=np.float32)
    faiss.normalize_L2(qvec)

    # Search — get more than needed to filter by threshold
    k = min(limit * 3, index.ntotal)
    scores, indices = index.search(qvec, k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(_id_map):
            continue
        if score < min_score:
            continue
        results.append((_id_map[idx], float(score)))

    return results[:limit]


def backfill_embeddings(api_key=None, batch_size=50):
    """Generate embeddings for all URLs missing them."""
    from sqlalchemy import not_

    missing = CanonicalUrl.query.filter(
        not_(CanonicalUrl.id.in_(
            db.session.query(UrlEmbedding.canonical_url_id)
        ))
    ).all()

    if not missing:
        return 0

    # Ensure vectorizer is ready for TF-IDF mode
    if not api_key:
        _get_tfidf_vectorizer()

    count = 0
    if api_key:
        # Batch OpenAI embeddings for speed
        for i in range(0, len(missing), batch_size):
            batch = missing[i:i + batch_size]
            texts = []
            batch_ids = []
            for cu in batch:
                text = build_text_for_url(cu.id)
                if text:
                    texts.append(text)
                    batch_ids.append((cu.id, text))

            if not texts:
                continue

            vectors = generate_embeddings_batch(texts, api_key)
            if not vectors:
                # Fallback to individual
                for cu_id, text in batch_ids:
                    embed_url(cu_id, api_key=api_key)
                    count += 1
                continue

            for (cu_id, text), vector in zip(batch_ids, vectors):
                emb = UrlEmbedding(
                    canonical_url_id=cu_id,
                    text_content=text[:2000],
                    model="openai",
                    dimensions=len(vector),
                )
                emb.set_vector(vector)
                db.session.add(emb)
                count += 1

            db.session.commit()
    else:
        for cu in missing:
            if embed_url(cu.id):
                count += 1
            if count % 100 == 0 and count > 0:
                db.session.commit()
        db.session.commit()

    # Rebuild FAISS index with all embeddings
    with _lock:
        _build_faiss_index()

    return count


# Legacy aliases
_backfill_embeddings_if_needed = lambda: backfill_embeddings()
_get_tfidf_vectorizer_compat = _get_tfidf_vectorizer
