from datetime import datetime, timedelta

from flask import jsonify, request

from app import db
from app.api.v2 import bp
from app.api.v2.auth import token_or_key_auth
from app.models import (
    CanonicalUrl, Post, Topic, TopicSubscription, UrlMetadata, UrlTopicScore,
)


@bp.route("/topics/<int:topic_id>/feed", methods=["GET"])
def get_topic_feed(topic_id):
    """Curated, ranked feed of URLs for a topic.

    Query params:
      limit: max results (default 25)
      min_score: minimum quality threshold (default 0.0)
      period: time window e.g. '7d', '48h'
      page: page number (default 1)
    """
    topic = Topic.query.get_or_404(topic_id)
    limit = min(request.args.get("limit", 25, type=int), 100)
    min_score = request.args.get("min_score", 0.0, type=float)
    period = request.args.get("period", "7d")
    page = request.args.get("page", 1, type=int)

    hours = _parse_period(period) or 168
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    query = (
        db.session.query(UrlTopicScore, CanonicalUrl)
        .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .filter(
            UrlTopicScore.topic_id == topic_id,
            UrlTopicScore.combined_score >= min_score,
            UrlTopicScore.first_tagged_at >= cutoff,
        )
        .order_by(UrlTopicScore.combined_score.desc())
    )

    # Manual pagination
    total = query.count()
    results = query.offset((page - 1) * limit).limit(limit).all()

    feed_items = []
    for uts, cu in results:
        # Get title from best post
        sample_post = Post.query.filter_by(canonical_url_id=cu.id).first()
        # Get metadata
        meta = UrlMetadata.query.filter_by(canonical_url_id=cu.id).first()

        feed_items.append({
            "canonical_url": cu.canonical_url,
            "canonical_url_id": cu.id,
            "domain": cu.domain,
            "title": sample_post.body if sample_post else None,
            "summary": meta.summary if meta else None,
            "sentiment": meta.sentiment if meta else None,
            "combined_score": uts.combined_score,
            "submission_count": cu.submission_count,
            "first_tagged_at": uts.first_tagged_at.isoformat() + "Z" if uts.first_tagged_at else None,
        })

    return jsonify({
        "topic_id": topic_id,
        "topic_name": topic.name,
        "period": period,
        "page": page,
        "total": total,
        "count": len(feed_items),
        "feed": feed_items,
    })


@bp.route("/feed/personalized", methods=["GET"])
@token_or_key_auth.login_required
def get_personalized_feed():
    """Personalized feed based on user's topic subscriptions."""
    user = token_or_key_auth.current_user()
    limit = min(request.args.get("limit", 25, type=int), 100)
    period = request.args.get("period", "48h")

    hours = _parse_period(period) or 48
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    subs = TopicSubscription.query.filter_by(user_id=user.id, is_active=True).all()
    if not subs:
        return jsonify({"message": "no active subscriptions", "feed": []})

    all_items = []
    for sub in subs:
        results = (
            db.session.query(UrlTopicScore, CanonicalUrl)
            .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
            .filter(
                UrlTopicScore.topic_id == sub.topic_id,
                UrlTopicScore.combined_score >= sub.min_score,
                UrlTopicScore.first_tagged_at >= cutoff,
            )
            .order_by(UrlTopicScore.combined_score.desc())
            .limit(10)
            .all()
        )

        for uts, cu in results:
            sample_post = Post.query.filter_by(canonical_url_id=cu.id).first()
            all_items.append({
                "canonical_url": cu.canonical_url,
                "canonical_url_id": cu.id,
                "domain": cu.domain,
                "title": sample_post.body if sample_post else None,
                "topic_id": sub.topic_id,
                "topic_name": sub.topic.name if sub.topic else None,
                "combined_score": uts.combined_score,
                "submission_count": cu.submission_count,
            })

    # Sort by score descending, deduplicate
    seen = set()
    deduped = []
    for item in sorted(all_items, key=lambda x: x["combined_score"], reverse=True):
        if item["canonical_url_id"] not in seen:
            seen.add(item["canonical_url_id"])
            deduped.append(item)

    return jsonify({
        "subscription_count": len(subs),
        "period": period,
        "count": len(deduped[:limit]),
        "feed": deduped[:limit],
    })


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
