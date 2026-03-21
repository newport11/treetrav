from datetime import datetime

from flask import abort, jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import token_or_key_auth
from app.models import (
    CanonicalUrl, Post, PostTopicTag, Topic, UrlPropagation, UrlTopicScore, User,
)
from app.services.scoring import recompute_url_topic_score


@bp.route("/posts/<int:post_id>/tag", methods=["POST"])
@token_or_key_auth.login_required
def tag_post(post_id):
    """Tag a post with one or more topics."""
    user = token_or_key_auth.current_user()
    post = Post.query.get_or_404(post_id)
    data = request.get_json() or {}

    topics = data.get("topics", [])
    if not topics:
        topic_id = data.get("topic_id")
        if topic_id:
            topics = [{"topic_id": topic_id, "confidence": data.get("confidence", 1.0)}]
        else:
            return bad_request("must include topics list or topic_id")

    tagged = []
    for t in topics:
        tid = t.get("topic_id") if isinstance(t, dict) else t
        confidence = t.get("confidence", 1.0) if isinstance(t, dict) else 1.0

        topic = Topic.query.get(tid)
        if not topic:
            continue

        existing = PostTopicTag.query.filter_by(
            post_id=post_id, topic_id=tid, tagged_by=user.id
        ).first()
        if existing:
            existing.confidence = confidence
        else:
            tag = PostTopicTag(
                post_id=post_id, topic_id=tid,
                tagged_by=user.id, confidence=confidence,
            )
            db.session.add(tag)

        # Ensure UrlTopicScore exists for this URL+topic
        if post.canonical_url_id:
            score = UrlTopicScore.query.filter_by(
                canonical_url_id=post.canonical_url_id, topic_id=tid
            ).first()
            if not score:
                score = UrlTopicScore(
                    canonical_url_id=post.canonical_url_id, topic_id=tid
                )
                db.session.add(score)

            # Track propagation
            prop = UrlPropagation.query.filter_by(
                canonical_url_id=post.canonical_url_id, topic_id=tid
            ).first()
            if not prop:
                prop = UrlPropagation(
                    canonical_url_id=post.canonical_url_id,
                    topic_id=tid,
                    first_submitted_by=user.id,
                )
                db.session.add(prop)

            # Update topic url_count
            topic.url_count = (topic.url_count or 0) + 1

        tagged.append({"topic_id": tid, "confidence": confidence})

    db.session.commit()

    # Recompute scores for tagged topics
    if post.canonical_url_id:
        for t in tagged:
            recompute_url_topic_score(post.canonical_url_id, t["topic_id"])

    return jsonify({"post_id": post_id, "tagged": tagged})


@bp.route("/posts/<int:post_id>/tag/<int:topic_id>", methods=["DELETE"])
@token_or_key_auth.login_required
def untag_post(post_id, topic_id):
    """Remove a topic tag from a post."""
    user = token_or_key_auth.current_user()
    tag = PostTopicTag.query.filter_by(
        post_id=post_id, topic_id=topic_id, tagged_by=user.id
    ).first_or_404()
    db.session.delete(tag)
    db.session.commit()

    # Recompute score
    post = Post.query.get(post_id)
    if post and post.canonical_url_id:
        recompute_url_topic_score(post.canonical_url_id, topic_id)

    return jsonify({"status": "removed"})


@bp.route("/posts/<int:post_id>/tags", methods=["GET"])
def get_post_tags(post_id):
    """List all topic tags on a post."""
    post = Post.query.get_or_404(post_id)
    tags = PostTopicTag.query.filter_by(post_id=post_id).all()
    return jsonify({
        "post_id": post_id,
        "tags": [t.to_dict() for t in tags],
    })


@bp.route("/topics/<int:topic_id>/top-urls", methods=["GET"])
def get_top_urls_for_topic(topic_id):
    """Get top URLs for a topic, with optional filters."""
    topic = Topic.query.get_or_404(topic_id)
    limit = min(request.args.get("limit", 10, type=int), 100)
    min_trust = request.args.get("min_trust", 0.0, type=float)
    period = request.args.get("period", "")

    # Rollup: include this topic + all descendant topics
    def get_descendant_ids(t):
        ids = [t.id]
        for child in t.children:
            if child.is_active:
                ids.extend(get_descendant_ids(child))
        return ids

    all_topic_ids = get_descendant_ids(topic)

    query = (
        db.session.query(UrlTopicScore, CanonicalUrl)
        .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .filter(UrlTopicScore.topic_id.in_(all_topic_ids))
    )

    # Time filter
    if period:
        from datetime import timedelta
        hours = _parse_period(period)
        if hours:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            query = query.filter(UrlTopicScore.first_tagged_at >= cutoff)

    # Trust filter — only include URLs where at least one submitter has trust >= min_trust
    if min_trust > 0:
        from sqlalchemy import exists
        query = query.filter(
            exists().where(
                db.and_(
                    Post.canonical_url_id == CanonicalUrl.id,
                    User.id == Post.user_id,
                    User.trust_score >= min_trust,
                )
            )
        )

    results = (
        query.order_by(UrlTopicScore.combined_score.desc())
        .limit(limit)
        .all()
    )

    return jsonify({
        "topic_id": topic_id,
        "topic_name": topic.name,
        "count": len(results),
        "urls": [
            {
                "canonical_url": cu.canonical_url,
                "canonical_url_id": cu.id,
                "domain": cu.domain,
                "submission_count": cu.submission_count,
                "relevance_score": uts.relevance_score,
                "quality_score": uts.quality_score,
                "combined_score": uts.combined_score,
                "vote_count": uts.vote_count,
                "first_tagged_at": uts.first_tagged_at.isoformat() + "Z" if uts.first_tagged_at else None,
            }
            for uts, cu in results
        ],
    })


def _parse_period(period_str):
    """Parse period string like '48h', '7d', '30m' to hours."""
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
