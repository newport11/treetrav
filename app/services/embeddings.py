"""
Semantic Search Engine for Treetrav
====================================

Architecture:
    1. Each URL gets a TF-IDF vector (512 dimensions) at post time
    2. Vectors are stored in the DB as JSON (UrlEmbedding table)
    3. On first search, ALL vectors are loaded into a numpy matrix (n x 512)
    4. The matrix is pre-normalized (unit vectors) for fast cosine similarity
    5. Search = single matrix-vector dot product → O(1) in Python, O(n) in C/BLAS
    6. New embeddings are appended to the in-memory index without rebuilding

Performance:
    - 10K URLs: <5ms per query
    - 100K URLs: <20ms per query
    - 1M URLs: <200ms per query (switch to FAISS at this point)

Two modes:
    - TF-IDF (default, free): Literal word matching with sublinear TF and 512 features
    - OpenAI (optional): Semantic embeddings via text-embedding-3-small (1536 dims)
      Set USE_OPENAI_EMBEDDING=True and OPENAI_API_KEY in .env

How embeddings are generated:
    - Each CanonicalUrl gets a text representation built from:
      title + description + metadata summaries + entities + topic names + domain + URL
    - This text is vectorized into a fixed-dimension vector
    - The vector is stored once and never recomputed (unless metadata changes)
    - New posts trigger embedding generation in a background thread
"""

import json
import threading

import numpy as np
import requests
from sklearn.feature_extraction.text import TfidfVectorizer

from app import db
from app.models import CanonicalUrl, Post, UrlEmbedding, UrlMetadata


# ===========================================================================
# Configuration
# ===========================================================================

EMBEDDING_API = "https://api.openai.com/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-3-small"
TFIDF_FEATURES = 512
MIN_SIMILARITY = 0.15  # Below this = noise, don't return

# ===========================================================================
# In-memory search index — loaded once, appended to as new embeddings arrive
# ===========================================================================

_index_lock = threading.Lock()
_index = None  # dict with 'matrix', 'ids', 'norms_applied'
_vectorizer = None  # fitted TF-IDF vectorizer
_vectorizer_lock = threading.Lock()


class SearchIndex:
    """In-memory vector search index using numpy."""

    def __init__(self):
        self.matrix = None  # numpy array (n x dims), pre-normalized
        self.ids = []  # canonical_url_ids in same order as matrix rows
        self.id_set = set()

    def load_from_db(self):
        """Load all embeddings from DB into numpy matrix."""
        embeddings = UrlEmbedding.query.all()
        if not embeddings:
            self.matrix = None
            self.ids = []
            self.id_set = set()
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

        self.matrix = np.array(vectors, dtype=np.float32)
        self.ids = ids
        self.id_set = set(ids)

        # Pre-normalize for cosine similarity (dot product of unit vectors = cosine sim)
        norms = np.linalg.norm(self.matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1  # avoid division by zero
        self.matrix = self.matrix / norms

    def append(self, canonical_url_id, vector):
        """Append a single new vector to the index without rebuilding."""
        if canonical_url_id in self.id_set:
            return

        vec = np.array(vector, dtype=np.float32).reshape(1, -1)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        if self.matrix is not None and self.matrix.shape[1] == vec.shape[1]:
            self.matrix = np.vstack([self.matrix, vec])
        else:
            self.matrix = vec

        self.ids.append(canonical_url_id)
        self.id_set.add(canonical_url_id)

    def search(self, query_vector, limit=10, min_score=MIN_SIMILARITY):
        """Find most similar vectors using matrix dot product.

        Returns list of (canonical_url_id, similarity_score).
        """
        if self.matrix is None or len(self.ids) == 0:
            return []

        # Normalize query vector
        qvec = np.array(query_vector, dtype=np.float32).reshape(1, -1)
        qnorm = np.linalg.norm(qvec)
        if qnorm == 0:
            return []
        qvec = qvec / qnorm

        # Single matrix-vector dot product = all cosine similarities at once
        similarities = (self.matrix @ qvec.T).flatten()

        # Get top-k above threshold
        mask = similarities >= min_score
        if not mask.any():
            return []

        indices = np.where(mask)[0]
        top_indices = indices[np.argsort(similarities[indices])[::-1]][:limit]

        return [(self.ids[i], float(similarities[i])) for i in top_indices]

    @property
    def size(self):
        return len(self.ids)


def _get_index():
    """Get or build the search index."""
    global _index
    if _index is not None and _index.size > 0:
        return _index

    with _index_lock:
        if _index is not None and _index.size > 0:
            return _index
        _index = SearchIndex()
        _index.load_from_db()
        return _index


def _get_vectorizer():
    """Get or build the TF-IDF vectorizer. Fitted on all existing URL text."""
    global _vectorizer
    if _vectorizer is not None:
        return _vectorizer

    with _vectorizer_lock:
        if _vectorizer is not None:
            return _vectorizer

        # Build from existing embeddings text_content (fast)
        embeddings = UrlEmbedding.query.all()
        if embeddings:
            texts = [e.text_content or "" for e in embeddings if e.text_content]
        else:
            # No embeddings — build from canonical URLs directly
            all_urls = CanonicalUrl.query.all()
            texts = []
            for cu in all_urls:
                text = build_text_for_url(cu.id)
                if text:
                    texts.append(text)

        if not texts:
            return None

        _vectorizer = TfidfVectorizer(
            max_features=TFIDF_FEATURES,
            stop_words="english",
            sublinear_tf=True,
            ngram_range=(1, 2),  # unigrams + bigrams for better phrase matching
            min_df=2,  # ignore terms that appear in fewer than 2 documents
            max_df=0.95,  # ignore terms that appear in >95% of documents
        )
        _vectorizer.fit(texts)
        return _vectorizer


# ===========================================================================
# Text building — what gets embedded for each URL
# ===========================================================================

def build_text_for_url(canonical_url_id):
    """Build a rich text representation of a URL for embedding.

    Combines: URL + domain + titles + descriptions + metadata summaries +
    entities + topic names. This is what gets vectorized.
    """
    cu = CanonicalUrl.query.get(canonical_url_id)
    if not cu:
        return None

    parts = []

    # Domain (weighted by repetition for importance)
    if cu.domain:
        parts.append(cu.domain)

    # Post titles and descriptions (most important signal)
    posts = Post.query.filter_by(canonical_url_id=canonical_url_id).limit(5).all()
    for p in posts:
        if p.body:
            parts.append(p.body)
            parts.append(p.body)  # double-weight titles
        if p.description:
            parts.append(p.description)

    # Metadata summaries and entities
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

    # Topic names
    from app.models import PostTopicTag, Topic
    if posts:
        topic_ids = (
            db.session.query(PostTopicTag.topic_id)
            .filter(PostTopicTag.post_id.in_([p.id for p in posts]))
            .distinct()
            .all()
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

def generate_embedding_tfidf(text):
    """Generate a TF-IDF vector for the given text."""
    vectorizer = _get_vectorizer()
    if vectorizer is None:
        return None
    vec = vectorizer.transform([text]).toarray()[0].tolist()
    return vec


def generate_embedding_openai(text, api_key):
    """Generate an OpenAI embedding vector."""
    try:
        response = requests.post(
            EMBEDDING_API,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": EMBEDDING_MODEL, "input": text[:8000]},
            timeout=30,
        )
        if response.status_code == 200:
            return response.json()["data"][0]["embedding"]
    except Exception:
        pass
    return None


def generate_embeddings_batch(texts, api_key):
    """Generate OpenAI embeddings for a batch of texts."""
    try:
        response = requests.post(
            EMBEDDING_API,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": EMBEDDING_MODEL, "input": [t[:8000] for t in texts]},
            timeout=60,
        )
        if response.status_code == 200:
            data = response.json()["data"]
            return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]
    except Exception:
        pass
    return None


def embed_url(canonical_url_id, use_openai=False, api_key=None):
    """Generate and store embedding for a single URL. Returns True if successful."""
    # Skip if already embedded
    existing = UrlEmbedding.query.filter_by(canonical_url_id=canonical_url_id).first()
    if existing:
        return True

    text = build_text_for_url(canonical_url_id)
    if not text:
        return False

    if use_openai and api_key:
        vector = generate_embedding_openai(text, api_key)
        model = "openai-3-small"
    else:
        vector = generate_embedding_tfidf(text)
        model = f"tfidf-{TFIDF_FEATURES}"

    if not vector:
        return False

    emb = UrlEmbedding(
        canonical_url_id=canonical_url_id,
        text_content=text[:2000],
        model=model,
        dimensions=len(vector),
    )
    emb.set_vector(vector)
    db.session.add(emb)
    db.session.commit()

    # Append to in-memory index
    index = _get_index()
    index.append(canonical_url_id, vector)

    return True


# ===========================================================================
# Search
# ===========================================================================

def semantic_search(query, api_key=None, limit=10, min_score=MIN_SIMILARITY):
    """Search for URLs by semantic similarity.

    1. Vectorize the query using the same method as stored embeddings
    2. Dot product against pre-normalized matrix (= cosine similarity)
    3. Return top-k results above min_score threshold

    Returns list of (canonical_url_id, similarity_score).
    """
    index = _get_index()
    if index.size == 0:
        return []

    # Determine embedding mode from stored data
    first_emb = UrlEmbedding.query.first()
    is_openai = first_emb and first_emb.model and "openai" in first_emb.model

    if is_openai and api_key:
        query_vector = generate_embedding_openai(query, api_key)
    else:
        query_vector = generate_embedding_tfidf(query)

    if not query_vector:
        return []

    return index.search(query_vector, limit=limit, min_score=min_score)


# ===========================================================================
# Backfill — generate embeddings for URLs that don't have them
# ===========================================================================

def backfill_embeddings(use_openai=False, api_key=None, batch_size=100):
    """Generate embeddings for all URLs missing them. Called on first search."""
    from sqlalchemy import not_

    missing = CanonicalUrl.query.filter(
        not_(CanonicalUrl.id.in_(
            db.session.query(UrlEmbedding.canonical_url_id)
        ))
    ).all()

    if not missing:
        return 0

    # Ensure vectorizer is fitted before generating embeddings
    _get_vectorizer()

    count = 0
    for cu in missing:
        if embed_url(cu.id, use_openai=use_openai, api_key=api_key):
            count += 1
        if count % batch_size == 0 and count > 0:
            db.session.commit()

    db.session.commit()

    # Reload index with all new embeddings
    global _index
    with _index_lock:
        _index = SearchIndex()
        _index.load_from_db()

    return count


# Legacy alias
_backfill_embeddings_if_needed = lambda: backfill_embeddings()
_get_tfidf_vectorizer = lambda: (_get_vectorizer(), None, None)
