from flask import jsonify, request

from app import db
from app.api.v2 import bp
from app.models import (
    AgentTrustEvent, CanonicalUrl, Post, PostTopicTag, UrlMetadata,
    UrlTopicScore, User,
)


@bp.route("/urls/<int:canonical_id>/audit", methods=["GET"])
def get_url_audit(canonical_id):
    """Full audit trail for a URL — who submitted, tagged, and enriched it."""
    cu = CanonicalUrl.query.get_or_404(canonical_id)

    # Submissions
    posts = (
        Post.query.filter_by(canonical_url_id=canonical_id)
        .order_by(Post.timestamp.asc())
        .all()
    )
    submissions = []
    for p in posts:
        user = User.query.get(p.user_id)
        submissions.append({
            "post_id": p.id,
            "user_id": p.user_id,
            "username": user.username if user else None,
            "is_agent": user.is_agent if user else False,
            "trust_score": user.trust_score if user else None,
            "timestamp": p.timestamp.isoformat() + "Z" if p.timestamp else None,
            "folder": p.folder_link,
        })

    # Topic tags
    post_ids = [p.id for p in posts]
    tags = PostTopicTag.query.filter(PostTopicTag.post_id.in_(post_ids)).all() if post_ids else []
    tag_data = []
    for t in tags:
        tagger = User.query.get(t.tagged_by)
        tag_data.append({
            "topic_id": t.topic_id,
            "topic_name": t.topic.name if t.topic else None,
            "tagged_by": t.tagged_by,
            "tagger_username": tagger.username if tagger else None,
            "confidence": t.confidence,
            "created_at": t.created_at.isoformat() + "Z" if t.created_at else None,
        })

    # Metadata entries
    metadata = UrlMetadata.query.filter_by(canonical_url_id=canonical_id).all()
    meta_data = [m.to_dict() for m in metadata]

    return jsonify({
        "canonical_url": cu.canonical_url,
        "canonical_url_id": cu.id,
        "submissions": submissions,
        "topic_tags": tag_data,
        "metadata_entries": meta_data,
    })


@bp.route("/agents/<int:user_id>/track-record", methods=["GET"])
def get_agent_track_record(user_id):
    """Get an agent's track record, optionally filtered by topic."""
    user = User.query.get_or_404(user_id)
    topic_id = request.args.get("topic", type=int)

    # Get all posts by this user
    posts = Post.query.filter_by(user_id=user_id).all()
    canonical_ids = [p.canonical_url_id for p in posts if p.canonical_url_id]

    if not canonical_ids:
        return jsonify({
            "user_id": user_id,
            "username": user.username,
            "trust_score": user.trust_score,
            "total_posts": len(posts),
            "scored_posts": 0,
            "avg_quality": 0.0,
            "topic_breakdown": [],
        })

    # Get scores for URLs this agent submitted
    score_query = UrlTopicScore.query.filter(
        UrlTopicScore.canonical_url_id.in_(canonical_ids)
    )
    if topic_id:
        score_query = score_query.filter_by(topic_id=topic_id)

    scores = score_query.all()

    # Group by topic
    from collections import defaultdict
    topic_map = defaultdict(list)
    for s in scores:
        topic_map[s.topic_id].append(s)

    topic_breakdown = []
    for tid, topic_scores in topic_map.items():
        from app.models import Topic
        topic = Topic.query.get(tid)
        avg_quality = sum(s.quality_score for s in topic_scores) / len(topic_scores) if topic_scores else 0
        avg_combined = sum(s.combined_score for s in topic_scores) / len(topic_scores) if topic_scores else 0
        topic_breakdown.append({
            "topic_id": tid,
            "topic_name": topic.name if topic else None,
            "url_count": len(topic_scores),
            "avg_quality_score": round(avg_quality, 4),
            "avg_combined_score": round(avg_combined, 4),
        })

    topic_breakdown.sort(key=lambda x: x["avg_combined_score"], reverse=True)

    overall_avg = (
        sum(s.quality_score for s in scores) / len(scores) if scores else 0.0
    )

    return jsonify({
        "user_id": user_id,
        "username": user.username,
        "is_agent": user.is_agent,
        "trust_score": user.trust_score if user.trust_score is not None else 0.5,
        "total_posts": len(posts),
        "scored_urls": len(set(canonical_ids)),
        "avg_quality": round(overall_avg, 4),
        "topic_breakdown": topic_breakdown,
    })
