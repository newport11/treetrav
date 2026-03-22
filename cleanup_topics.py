"""Clean up duplicate topics from multiple backfill runs.
Keeps the ROOT-level topics and merges the Business/seeded duplicates into them."""
from app import create_app, db
from app.models import (
    DomainCredibility, Post, PostTopicTag, Topic, TopicAlias,
    TopicSubscription, UrlPropagation, UrlTopicScore,
)

app = create_app()


def merge_topic(source_id, target_id):
    """Move all references from source topic to target, then delete source."""
    print(f"  Merging topic {source_id} -> {target_id}")

    # Move post tags
    PostTopicTag.query.filter_by(topic_id=source_id).update(
        {"topic_id": target_id}, synchronize_session=False
    )

    # Move URL scores (merge if both exist)
    for score in UrlTopicScore.query.filter_by(topic_id=source_id).all():
        existing = UrlTopicScore.query.filter_by(
            canonical_url_id=score.canonical_url_id, topic_id=target_id
        ).first()
        if existing:
            existing.vote_count = (existing.vote_count or 0) + (score.vote_count or 0)
            existing.combined_score = max(existing.combined_score or 0, score.combined_score or 0)
            db.session.delete(score)
        else:
            score.topic_id = target_id

    # Move propagations
    for prop in UrlPropagation.query.filter_by(topic_id=source_id).all():
        existing = UrlPropagation.query.filter_by(
            canonical_url_id=prop.canonical_url_id, topic_id=target_id
        ).first()
        if existing:
            db.session.delete(prop)
        else:
            prop.topic_id = target_id

    # Move subscriptions
    for sub in TopicSubscription.query.filter_by(topic_id=source_id).all():
        existing = TopicSubscription.query.filter_by(
            user_id=sub.user_id, topic_id=target_id
        ).first()
        if existing:
            db.session.delete(sub)
        else:
            sub.topic_id = target_id

    # Move domain credibility
    for dc in DomainCredibility.query.filter_by(topic_id=source_id).all():
        existing = DomainCredibility.query.filter_by(
            domain=dc.domain, topic_id=target_id
        ).first()
        if existing:
            db.session.delete(dc)
        else:
            dc.topic_id = target_id

    # Move children to target
    for child in Topic.query.filter_by(parent_id=source_id).all():
        # Check if target already has a child with the same name
        existing_child = Topic.query.filter(
            db.func.lower(Topic.name) == child.name.lower(),
            Topic.parent_id == target_id,
        ).first()
        if existing_child:
            merge_topic(child.id, existing_child.id)
        else:
            child.parent_id = target_id

    # Transfer url_count
    source = Topic.query.get(source_id)
    target = Topic.query.get(target_id)
    if source and target:
        target.url_count = (target.url_count or 0) + (source.url_count or 0)

    # Delete source
    TopicAlias.query.filter_by(topic_id=source_id).delete()
    if source:
        db.session.delete(source)

    db.session.flush()


with app.app_context():
    # Find duplicate root topics that also exist under Business
    # Keep the ROOT versions, merge the Business children into them

    business = Topic.query.filter_by(name="Business", parent_id=None).first()
    if not business:
        print("No Business topic found")
    else:
        print(f"Business topic id={business.id}")

    # Find all root topics
    root_topics = Topic.query.filter_by(parent_id=None).all()
    print(f"Root topics: {[t.name for t in root_topics]}")

    # For each root topic, check if Business has a child with the same name
    if business:
        for root_topic in root_topics:
            if root_topic.id == business.id:
                continue
            business_child = Topic.query.filter(
                db.func.lower(Topic.name) == root_topic.name.lower(),
                Topic.parent_id == business.id,
            ).first()
            if business_child:
                print(f"\nDuplicate found: '{root_topic.name}'")
                print(f"  ROOT id={root_topic.id} (urls={root_topic.url_count})")
                print(f"  Business child id={business_child.id} (urls={business_child.url_count})")
                merge_topic(business_child.id, root_topic.id)

    # Also check for duplicate Startups at root vs under Business
    seeded_startups = Topic.query.filter_by(name="Startups", parent_id=business.id if business else None).first()
    root_startups = Topic.query.filter_by(name="Startups", parent_id=None).first()
    if seeded_startups and root_startups and seeded_startups.id != root_startups.id:
        print(f"\nDuplicate Startups found")
        merge_topic(seeded_startups.id, root_startups.id)

    db.session.commit()

    # Remove empty seeded topics that have no URLs and no children
    empty = Topic.query.filter(
        Topic.url_count <= 0,
        ~Topic.children.any(),
    ).all()
    print(f"\nEmpty leaf topics: {len(empty)}")
    for t in empty:
        # Only delete if truly orphaned (no tags, no scores)
        tags = PostTopicTag.query.filter_by(topic_id=t.id).count()
        scores = UrlTopicScore.query.filter_by(topic_id=t.id).count()
        if tags == 0 and scores == 0:
            print(f"  Deleting empty topic: {t.name} (parent={t.parent.name if t.parent else 'ROOT'})")
            db.session.delete(t)

    db.session.commit()

    remaining = Topic.query.count()
    roots = Topic.query.filter_by(parent_id=None).count()
    print(f"\nDone. {remaining} topics remaining ({roots} root topics)")
