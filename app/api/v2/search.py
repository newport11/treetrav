from datetime import datetime, timedelta

from flask import current_app, jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import _resolve_user
from app.models import AgentQueryLog, CanonicalUrl, Post, Topic, UrlEmbedding, UrlMetadata, UrlTopicScore


def _log_query(endpoint, query_text=None, topic_id=None, canonical_url_id=None):
    """Log a query for session inference. Silent fail — never block the request."""
    try:
        user = _resolve_user()
        if user:
            log = AgentQueryLog(
                user_id=user.id,
                endpoint=endpoint,
                query_text=query_text,
                topic_id=topic_id,
                canonical_url_id=canonical_url_id,
            )
            db.session.add(log)
            db.session.commit()
    except Exception:
        pass


@bp.route("/search", methods=["GET"])
def search_urls():
    """Search for URLs by natural language query.

    Uses vector embeddings for semantic search when available (requires OPENAI_API_KEY).
    Falls back to keyword matching against topic names, post titles, and metadata.

    Query params:
      q: search query (required)
      limit: max results (default 10)
      period: time filter e.g. '48h', '7d'
      min_score: minimum similarity threshold (default 0.0)
      topic: restrict to a specific topic ID
    """
    q = request.args.get("q", "").strip()
    if not q:
        return bad_request("must include q parameter")

    limit = min(request.args.get("limit", 10, type=int), 100)
    min_score = request.args.get("min_score", 0.0, type=float)
    period = request.args.get("period", "")

    # Parse natural language time phrases from the query itself
    if not period:
        period, q = _extract_time_from_query(q)

    # Support topic by ID or name
    topic_param = request.args.get("topic", "").strip()
    topic_id = None
    if topic_param:
        try:
            topic_id = int(topic_param)
        except ValueError:
            # Search by name (fuzzy)
            match = Topic.query.filter(
                Topic.name.ilike(f"%{topic_param}%"), Topic.is_active == True
            ).first()
            if match:
                topic_id = match.id

    use_openai = current_app.config.get("USE_OPENAI_EMBEDDING", False)
    api_key = current_app.config.get("OPENAI_API_KEY") if use_openai else None
    has_embeddings = UrlEmbedding.query.first() is not None

    # Auto-backfill embeddings if none exist but there are URLs
    if not has_embeddings:
        from app.models import CanonicalUrl
        if CanonicalUrl.query.first() is not None:
            from app.services.embeddings import _backfill_embeddings_if_needed
            _backfill_embeddings_if_needed()
            has_embeddings = UrlEmbedding.query.first() is not None

    # Log query for session inference
    _log_query("search", query_text=q, topic_id=topic_id)

    # Use vector search if embeddings exist (TF-IDF works locally, OpenAI needs API key)
    if has_embeddings:
        return _vector_search(q, api_key, limit, min_score, topic_id, period)

    # Fallback to keyword search
    return _keyword_search(q, limit, min_score, topic_id, period)


def _vector_search(q, api_key, limit, min_score, topic_id, period):
    """Semantic search using OpenAI embeddings + cosine similarity."""
    from app.services.embeddings import semantic_search

    # When filtering by topic, widen the candidate pool since many top results may not match
    candidate_limit = limit * 50 if topic_id else limit * 3
    raw_results = semantic_search(q, api_key, limit=candidate_limit, min_score=min_score)

    if not raw_results:
        # Fall back to keyword if embedding fails
        return _keyword_search(q, limit, min_score, topic_id, period)

    results = []
    for canonical_url_id, similarity in raw_results:
        cu = CanonicalUrl.query.get(canonical_url_id)
        if not cu:
            continue

        # Apply topic filter
        if topic_id:
            score = UrlTopicScore.query.filter_by(
                canonical_url_id=canonical_url_id, topic_id=topic_id
            ).first()
            if not score:
                continue

        # Apply period filter
        if period:
            hours = _parse_period(period)
            if hours:
                cutoff = datetime.utcnow() - timedelta(hours=hours)
                if cu.first_seen and cu.first_seen < cutoff:
                    recent_score = UrlTopicScore.query.filter(
                        UrlTopicScore.canonical_url_id == canonical_url_id,
                        UrlTopicScore.first_tagged_at >= cutoff,
                    ).first()
                    if not recent_score:
                        continue

        # Get title and metadata
        sample_post = Post.query.filter_by(canonical_url_id=canonical_url_id).first()
        meta = UrlMetadata.query.filter_by(canonical_url_id=canonical_url_id).first()

        # Get topic info
        best_score = (
            UrlTopicScore.query.filter_by(canonical_url_id=canonical_url_id)
            .order_by(UrlTopicScore.combined_score.desc())
            .first()
        )
        topic_name = best_score.topic.name if best_score and best_score.topic else None
        best_topic_id = best_score.topic_id if best_score else None

        results.append({
            "canonical_url": cu.canonical_url,
            "canonical_url_id": cu.id,
            "domain": cu.domain,
            "title": sample_post.body if sample_post else None,
            "summary": meta.summary if meta else None,
            "topic": topic_name,
            "topic_id": best_topic_id,
            "similarity": round(similarity, 4),
            "combined_score": best_score.combined_score if best_score else 0.0,
            "submission_count": cu.submission_count,
        })

        if len(results) >= limit:
            break

    return jsonify({
        "query": q,
        "search_mode": "semantic",
        "count": len(results),
        "results": results,
    })


def _keyword_search(q, limit, min_score, topic_id, period):
    """Fallback keyword search using SQL LIKE matching."""
    pattern = f"%{q}%"

    # Find matching topics
    matching_topics = Topic.query.filter(
        db.or_(Topic.name.ilike(pattern), Topic.description.ilike(pattern)),
        Topic.is_active == True,
    ).all()
    topic_ids = [t.id for t in matching_topics]

    if topic_id:
        topic_ids = [topic_id]

    # Build query for scored URLs
    query = (
        db.session.query(UrlTopicScore, CanonicalUrl, Topic)
        .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .join(Topic, UrlTopicScore.topic_id == Topic.id)
        .filter(UrlTopicScore.combined_score >= min_score)
    )

    if topic_ids:
        query = query.filter(UrlTopicScore.topic_id.in_(topic_ids))

    if period:
        hours = _parse_period(period)
        if hours:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            query = query.filter(UrlTopicScore.first_tagged_at >= cutoff)

    scored_results = (
        query.order_by(UrlTopicScore.combined_score.desc())
        .limit(limit)
        .all()
    )

    results = []
    seen_urls = set()

    for uts, cu, topic in scored_results:
        if cu.id in seen_urls:
            continue
        seen_urls.add(cu.id)

        sample_post = Post.query.filter_by(canonical_url_id=cu.id).first()
        meta = UrlMetadata.query.filter_by(canonical_url_id=cu.id).first()

        results.append({
            "canonical_url": cu.canonical_url,
            "canonical_url_id": cu.id,
            "domain": cu.domain,
            "title": sample_post.body if sample_post else None,
            "summary": meta.summary if meta else None,
            "topic": topic.name,
            "topic_id": topic.id,
            "similarity": None,
            "combined_score": uts.combined_score,
            "submission_count": cu.submission_count,
        })

    # If no scored results, search post titles directly
    if not results:
        posts = (
            Post.query.filter(
                db.or_(
                    Post.body.ilike(pattern),
                    Post.description.ilike(pattern),
                    Post.link.ilike(pattern),
                )
            )
            .order_by(Post.timestamp.desc())
            .limit(limit)
            .all()
        )
        for post in posts:
            results.append({
                "canonical_url": post.link,
                "canonical_url_id": post.canonical_url_id,
                "domain": None,
                "title": post.body,
                "summary": post.description,
                "topic": None,
                "topic_id": None,
                "similarity": None,
                "combined_score": 0.0,
                "submission_count": 1,
            })

    return jsonify({
        "query": q,
        "search_mode": "keyword",
        "count": len(results),
        "results": results,
    })


@bp.route("/search/topics", methods=["GET"])
def search_topics_v2():
    """Find topics matching a query."""
    q = request.args.get("q", "").strip()
    if not q:
        return bad_request("must include q parameter")

    pattern = f"%{q}%"
    topics = Topic.query.filter(
        db.or_(
            Topic.name.ilike(pattern),
            Topic.slug.ilike(pattern),
            Topic.description.ilike(pattern),
        ),
        Topic.is_active == True,
    ).limit(20).all()

    return jsonify({
        "query": q,
        "results": [t.to_dict() for t in topics],
    })


@bp.route("/search/urls", methods=["GET"])
def search_urls_in_topic():
    """Search URLs within a specific topic."""
    q = request.args.get("q", "").strip()
    topic_id = request.args.get("topic", type=int)
    if not q:
        return bad_request("must include q parameter")

    limit = min(request.args.get("limit", 10, type=int), 100)
    pattern = f"%{q}%"

    query = (
        db.session.query(UrlTopicScore, CanonicalUrl)
        .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .filter(CanonicalUrl.canonical_url.ilike(pattern))
    )

    if topic_id:
        query = query.filter(UrlTopicScore.topic_id == topic_id)

    results = query.order_by(UrlTopicScore.combined_score.desc()).limit(limit).all()

    return jsonify({
        "query": q,
        "topic_id": topic_id,
        "results": [
            {
                "canonical_url": cu.canonical_url,
                "canonical_url_id": cu.id,
                "combined_score": uts.combined_score,
                "topic_id": uts.topic_id,
            }
            for uts, cu in results
        ],
    })


def _extract_time_from_query(query):
    """Extract time phrases from natural language query. Returns (period, cleaned_query)."""
    import re

    patterns = [
        # "in the past/last X hours/days/weeks/months/years"
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+(\d+)\s+hours?\b', lambda m: f"{m.group(1)}h"),
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+(\d+)\s+days?\b', lambda m: f"{int(m.group(1)) * 24}h"),
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+(\d+)\s+weeks?\b', lambda m: f"{int(m.group(1)) * 168}h"),
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+(\d+)\s+months?\b', lambda m: f"{int(m.group(1)) * 720}h"),
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+(\d+)\s+years?\b', lambda m: f"{int(m.group(1)) * 8760}h"),
        # "in past/last hour/day/week/month/year" (no number)
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+hour\b', lambda m: "1h"),
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+day\b', lambda m: "24h"),
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+week\b', lambda m: "168h"),
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+month\b', lambda m: "720h"),
        (r'\b(?:in\s+the\s+)?(?:past|last)\s+year\b', lambda m: "8760h"),
        # "today"
        (r'\btoday\b', lambda m: "24h"),
        # "this week/month/year"
        (r'\bthis\s+week\b', lambda m: "168h"),
        (r'\bthis\s+month\b', lambda m: "720h"),
        (r'\bthis\s+year\b', lambda m: "8760h"),
        # "recently" / "recent"
        (r'\brecently\b', lambda m: "168h"),
        (r'\brecent\b', lambda m: "168h"),
        # "from the last X hours/days"
        (r'\bfrom\s+the\s+last\s+(\d+)\s+hours?\b', lambda m: f"{m.group(1)}h"),
        (r'\bfrom\s+the\s+last\s+(\d+)\s+days?\b', lambda m: f"{int(m.group(1)) * 24}h"),
    ]

    for pattern, handler in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            period = handler(match)
            cleaned = re.sub(pattern, '', query, flags=re.IGNORECASE).strip()
            # Clean up leftover prepositions and punctuation
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            cleaned = re.sub(r'\s+(in|from|from the|of the|during)\s*$', '', cleaned, flags=re.IGNORECASE).strip()
            cleaned = cleaned.rstrip(',').strip()
            return period, cleaned

    return "", query


def _parse_period(period_str):
    period_str = period_str.strip().lower()
    try:
        if period_str.endswith("h"):
            return int(period_str[:-1])
        elif period_str.endswith("d"):
            return int(period_str[:-1]) * 24
        elif period_str.endswith("m"):
            return int(period_str[:-1]) / 60.0
        else:
            return int(period_str)
    except (ValueError, IndexError):
        return None
