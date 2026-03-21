from datetime import datetime

from flask import jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import token_or_key_auth
from app.models import CanonicalUrl, Topic, TopicSubscription, UrlTopicScore, WebhookDelivery


@bp.route("/subscriptions", methods=["POST"])
@token_or_key_auth.login_required
def create_subscription():
    """Subscribe to a topic with quality threshold and optional webhook."""
    user = token_or_key_auth.current_user()
    data = request.get_json() or {}

    topic_id = data.get("topic_id")
    if not topic_id:
        return bad_request("must include topic_id")

    topic = Topic.query.get_or_404(topic_id)

    existing = TopicSubscription.query.filter_by(
        user_id=user.id, topic_id=topic_id
    ).first()
    if existing:
        return bad_request("already subscribed to this topic")

    sub = TopicSubscription(
        user_id=user.id,
        topic_id=topic_id,
        min_score=data.get("min_score", 0.5),
        webhook_url=data.get("webhook_url"),
        delivery_method=data.get("delivery_method", "poll"),
    )

    if sub.webhook_url:
        sub.delivery_method = "webhook"
        sub.generate_webhook_secret()

    db.session.add(sub)
    topic.subscriber_count = (topic.subscriber_count or 0) + 1
    db.session.commit()

    result = sub.to_dict()
    if sub.webhook_secret:
        result["webhook_secret"] = sub.webhook_secret
    return jsonify(result), 201


@bp.route("/subscriptions", methods=["GET"])
@token_or_key_auth.login_required
def list_subscriptions():
    """List current user's subscriptions."""
    user = token_or_key_auth.current_user()
    subs = TopicSubscription.query.filter_by(user_id=user.id, is_active=True).all()
    return jsonify({"subscriptions": [s.to_dict() for s in subs]})


@bp.route("/subscriptions/<int:sub_id>", methods=["PUT"])
@token_or_key_auth.login_required
def update_subscription(sub_id):
    """Update subscription threshold or webhook."""
    user = token_or_key_auth.current_user()
    sub = TopicSubscription.query.get_or_404(sub_id)
    if sub.user_id != user.id:
        return bad_request("not your subscription"), 403

    data = request.get_json() or {}
    if "min_score" in data:
        sub.min_score = data["min_score"]
    if "webhook_url" in data:
        sub.webhook_url = data["webhook_url"]
        if sub.webhook_url:
            sub.delivery_method = "webhook"
            sub.generate_webhook_secret()
    if "is_active" in data:
        sub.is_active = data["is_active"]

    db.session.commit()
    result = sub.to_dict()
    if sub.webhook_secret:
        result["webhook_secret"] = sub.webhook_secret
    return jsonify(result)


@bp.route("/subscriptions/<int:sub_id>", methods=["DELETE"])
@token_or_key_auth.login_required
def delete_subscription(sub_id):
    """Unsubscribe from a topic."""
    user = token_or_key_auth.current_user()
    sub = TopicSubscription.query.get_or_404(sub_id)
    if sub.user_id != user.id:
        return bad_request("not your subscription"), 403

    topic = Topic.query.get(sub.topic_id)
    if topic:
        topic.subscriber_count = max(0, (topic.subscriber_count or 0) - 1)

    db.session.delete(sub)
    db.session.commit()
    return jsonify({"status": "unsubscribed"})


@bp.route("/subscriptions/<int:sub_id>/feed", methods=["GET"])
@token_or_key_auth.login_required
def get_subscription_feed(sub_id):
    """Poll for new high-signal URLs since last check."""
    user = token_or_key_auth.current_user()
    sub = TopicSubscription.query.get_or_404(sub_id)
    if sub.user_id != user.id:
        return bad_request("not your subscription"), 403

    since = request.args.get("since")
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            since_dt = sub.last_delivered or sub.created_at
    else:
        since_dt = sub.last_delivered or sub.created_at

    limit = min(request.args.get("limit", 25, type=int), 100)

    results = (
        db.session.query(UrlTopicScore, CanonicalUrl)
        .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .filter(
            UrlTopicScore.topic_id == sub.topic_id,
            UrlTopicScore.combined_score >= sub.min_score,
            UrlTopicScore.first_tagged_at >= since_dt,
        )
        .order_by(UrlTopicScore.combined_score.desc())
        .limit(limit)
        .all()
    )

    sub.last_delivered = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "subscription_id": sub_id,
        "topic_id": sub.topic_id,
        "since": since_dt.isoformat() + "Z",
        "count": len(results),
        "urls": [
            {
                "canonical_url": cu.canonical_url,
                "canonical_url_id": cu.id,
                "domain": cu.domain,
                "combined_score": uts.combined_score,
                "submission_count": cu.submission_count,
                "first_tagged_at": uts.first_tagged_at.isoformat() + "Z" if uts.first_tagged_at else None,
            }
            for uts, cu in results
        ],
    })
