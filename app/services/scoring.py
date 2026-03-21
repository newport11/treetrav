from datetime import datetime, timedelta

from app import db
from app.models import (
    AgentTrustEvent, CanonicalUrl, DomainCredibility, Post, PostTopicTag,
    UrlPropagation, UrlTopicScore, User,
)


def recompute_url_topic_score(canonical_url_id, topic_id):
    """Recompute the combined score for a URL in a specific topic."""
    score_record = UrlTopicScore.query.filter_by(
        canonical_url_id=canonical_url_id, topic_id=topic_id
    ).first()
    if not score_record:
        return

    # Get all posts that reference this canonical URL and are tagged with this topic
    tags = (
        db.session.query(PostTopicTag, Post, User)
        .join(Post, PostTopicTag.post_id == Post.id)
        .join(User, Post.user_id == User.id)
        .filter(Post.canonical_url_id == canonical_url_id)
        .filter(PostTopicTag.topic_id == topic_id)
        .all()
    )

    if not tags:
        score_record.combined_score = 0.0
        score_record.vote_count = 0
        db.session.commit()
        return

    total_trust_weight = 0.0
    total_confidence = 0.0
    now = datetime.utcnow()

    for tag, post, user in tags:
        trust = user.trust_score if user.trust_score is not None else 0.3
        confidence = tag.confidence if tag.confidence else 1.0

        # Recency boost: posts from last 48h get a boost
        age_hours = (now - post.timestamp).total_seconds() / 3600.0 if post.timestamp else 999
        recency = max(0.1, 1.0 - (age_hours / 168.0))  # decay over 7 days

        total_trust_weight += trust * confidence * recency
        total_confidence += confidence

    vote_count = len(tags)
    # Normalize: more trusted, confident, recent votes → higher score
    quality = min(1.0, total_trust_weight / max(1.0, vote_count * 0.5))
    relevance = min(1.0, total_confidence / max(1.0, vote_count))

    combined = 0.4 * relevance + 0.4 * quality + 0.2 * min(1.0, vote_count / 10.0)

    score_record.relevance_score = round(relevance, 4)
    score_record.quality_score = round(quality, 4)
    score_record.combined_score = round(combined, 4)
    score_record.vote_count = vote_count
    score_record.last_updated_at = now
    db.session.commit()

    # Update global score on the canonical URL
    all_scores = UrlTopicScore.query.filter_by(canonical_url_id=canonical_url_id).all()
    if all_scores:
        canonical = CanonicalUrl.query.get(canonical_url_id)
        if canonical:
            canonical.global_score = round(
                max(s.combined_score for s in all_scores), 4
            )
            db.session.commit()


def recompute_all_scores_for_topic(topic_id):
    """Recompute all URL scores within a topic."""
    scores = UrlTopicScore.query.filter_by(topic_id=topic_id).all()
    for score in scores:
        recompute_url_topic_score(score.canonical_url_id, topic_id)


def update_agent_trust(user_id, event_type, delta, reason=None, post_id=None, topic_id=None):
    """Update an agent's trust score and log the event."""
    user = User.query.get(user_id)
    if not user:
        return

    event = AgentTrustEvent(
        user_id=user_id,
        event_type=event_type,
        delta=delta,
        reason=reason,
        related_post_id=post_id,
        related_topic_id=topic_id,
    )
    db.session.add(event)

    current_trust = user.trust_score if user.trust_score is not None else 0.3
    new_trust = max(0.0, min(1.0, current_trust + delta))
    user.trust_score = round(new_trust, 4)
    db.session.commit()


def recompute_domain_credibility(domain, topic_id=None):
    """Recompute credibility score for a domain (optionally within a topic)."""
    query = (
        db.session.query(UrlTopicScore)
        .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .filter(CanonicalUrl.domain == domain)
    )
    if topic_id:
        query = query.filter(UrlTopicScore.topic_id == topic_id)

    scores = query.all()
    if not scores:
        return

    avg_quality = sum(s.quality_score for s in scores) / len(scores)
    credibility = min(1.0, avg_quality * (1 + min(len(scores), 100) / 100.0) / 2.0)

    record = DomainCredibility.query.filter_by(domain=domain, topic_id=topic_id).first()
    if not record:
        record = DomainCredibility(domain=domain, topic_id=topic_id)
        db.session.add(record)

    record.credibility_score = round(credibility, 4)
    record.submission_count = len(scores)
    record.avg_quality_score = round(avg_quality, 4)
    db.session.commit()
