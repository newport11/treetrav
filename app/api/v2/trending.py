from datetime import datetime, timedelta

from flask import jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.models import CanonicalUrl, Post, UrlPropagation, UrlTopicScore, Topic


@bp.route("/trending", methods=["GET"])
def get_trending():
    """Get URLs trending across multiple topics simultaneously.

    Query params:
      min_topics: minimum number of topics (default 3)
      period: time window e.g. '24h', '7d'
      limit: max results (default 10)
    """
    min_topics = request.args.get("min_topics", 3, type=int)
    limit = min(request.args.get("limit", 10, type=int), 100)
    period = request.args.get("period", "24h")

    hours = _parse_period(period) or 24
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # Find URLs that appear in >= min_topics topics within the period
    from sqlalchemy import func

    cross_topic = (
        db.session.query(
            UrlTopicScore.canonical_url_id,
            func.count(UrlTopicScore.topic_id).label("topic_count"),
            func.avg(UrlTopicScore.combined_score).label("avg_score"),
        )
        .filter(UrlTopicScore.first_tagged_at >= cutoff)
        .group_by(UrlTopicScore.canonical_url_id)
        .having(func.count(UrlTopicScore.topic_id) >= min_topics)
        .order_by(func.avg(UrlTopicScore.combined_score).desc())
        .limit(limit)
        .all()
    )

    results = []
    for canonical_url_id, topic_count, avg_score in cross_topic:
        cu = CanonicalUrl.query.get(canonical_url_id)
        if not cu:
            continue

        # Get the topics it's trending in
        topic_scores = (
            UrlTopicScore.query
            .filter_by(canonical_url_id=canonical_url_id)
            .filter(UrlTopicScore.first_tagged_at >= cutoff)
            .all()
        )

        sample_post = Post.query.filter_by(canonical_url_id=canonical_url_id).first()

        results.append({
            "canonical_url": cu.canonical_url,
            "canonical_url_id": cu.id,
            "domain": cu.domain,
            "title": sample_post.body if sample_post else None,
            "topic_count": topic_count,
            "avg_score": round(float(avg_score), 4),
            "submission_count": cu.submission_count,
            "topics": [
                {
                    "topic_id": ts.topic_id,
                    "topic_name": ts.topic.name if ts.topic else None,
                    "combined_score": ts.combined_score,
                }
                for ts in topic_scores
            ],
        })

    return jsonify({
        "min_topics": min_topics,
        "period": period,
        "count": len(results),
        "trending": results,
    })


@bp.route("/urls/<int:canonical_id>/propagation", methods=["GET"])
def get_url_propagation(canonical_id):
    """Get propagation timeline — how a URL spread across topics over time."""
    cu = CanonicalUrl.query.get_or_404(canonical_id)

    propagations = (
        UrlPropagation.query
        .filter_by(canonical_url_id=canonical_id)
        .order_by(UrlPropagation.first_seen_in_topic.asc())
        .all()
    )

    # Also include which topics this URL is currently scored in
    topic_scores = (
        UrlTopicScore.query
        .filter_by(canonical_url_id=canonical_id)
        .order_by(UrlTopicScore.combined_score.desc())
        .all()
    )

    return jsonify({
        "canonical_url": cu.canonical_url,
        "canonical_url_id": cu.id,
        "first_seen": cu.first_seen.isoformat() + "Z" if cu.first_seen else None,
        "propagation_timeline": [p.to_dict() for p in propagations],
        "current_topic_scores": [s.to_dict() for s in topic_scores],
    })


@bp.route("/topics/<int:topic_id>/velocity", methods=["GET"])
def get_topic_velocity(topic_id):
    """Content velocity metrics for a topic."""
    topic = Topic.query.get_or_404(topic_id)
    period = request.args.get("period", "24h")
    hours = _parse_period(period) or 24
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    from sqlalchemy import func

    # Count new URLs tagged in this topic within the period
    new_count = (
        UrlTopicScore.query
        .filter_by(topic_id=topic_id)
        .filter(UrlTopicScore.first_tagged_at >= cutoff)
        .count()
    )

    # Average score of new URLs
    avg_score = (
        db.session.query(func.avg(UrlTopicScore.combined_score))
        .filter(
            UrlTopicScore.topic_id == topic_id,
            UrlTopicScore.first_tagged_at >= cutoff,
        )
        .scalar()
    ) or 0.0

    return jsonify({
        "topic_id": topic_id,
        "topic_name": topic.name,
        "period": period,
        "new_urls": new_count,
        "urls_per_hour": round(new_count / max(hours, 1), 2),
        "avg_score": round(float(avg_score), 4),
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
