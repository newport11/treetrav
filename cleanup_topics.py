"""Clean up ALL duplicate topics — finds any parent with duplicate children and merges them."""
from app import create_app, db
from app.models import (
    DomainCredibility, Post, PostTopicTag, Topic, TopicAlias,
    TopicSubscription, UrlPropagation, UrlTopicScore,
)

app = create_app()


def merge_topic(source_id, target_id):
    """Move all references from source topic to target, then delete source."""
    if source_id == target_id:
        return

    print(f"    Merging id={source_id} -> id={target_id}")

    # Move post tags — delete conflicts first
    for tag in PostTopicTag.query.filter_by(topic_id=source_id).all():
        existing = PostTopicTag.query.filter_by(
            post_id=tag.post_id, topic_id=target_id, tagged_by=tag.tagged_by
        ).first()
        if existing:
            db.session.delete(tag)
        else:
            tag.topic_id = target_id

    # Move URL scores
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

    db.session.flush()

    # Recursively merge children
    source = Topic.query.get(source_id)
    target = Topic.query.get(target_id)

    if source:
        for child in list(source.children):
            existing_child = Topic.query.filter(
                db.func.lower(Topic.name) == child.name.lower(),
                Topic.parent_id == target_id,
                Topic.id != child.id,
            ).first()
            if existing_child:
                merge_topic(child.id, existing_child.id)
            else:
                child.parent_id = target_id

        db.session.flush()

        # Transfer url_count
        if target:
            target.url_count = (target.url_count or 0) + (source.url_count or 0)

        # Delete aliases and source
        TopicAlias.query.filter_by(topic_id=source_id).delete()
        db.session.delete(source)
        db.session.flush()


with app.app_context():
    print("=== Finding ALL duplicate topics ===\n")

    # Get every parent (including None for root)
    parent_ids = set([t.parent_id for t in Topic.query.all()])

    total_merged = 0

    for parent_id in parent_ids:
        if parent_id is not None:
            children = Topic.query.filter_by(parent_id=parent_id).all()
        else:
            children = Topic.query.filter_by(parent_id=None).all()

        # Group by lowercase name
        name_groups = {}
        for child in children:
            key = child.name.lower()
            if key not in name_groups:
                name_groups[key] = []
            name_groups[key].append(child)

        # Merge duplicates (keep the one with lowest id)
        for name, group in name_groups.items():
            if len(group) > 1:
                parent_name = Topic.query.get(parent_id).name if parent_id else "ROOT"
                print(f"  Duplicate '{name}' under '{parent_name}': {[t.id for t in group]}")
                keep = min(group, key=lambda t: t.id)
                for dup in group:
                    if dup.id != keep.id:
                        merge_topic(dup.id, keep.id)
                        total_merged += 1

    db.session.commit()

    # Clean up empty topics with no children, no tags, no scores
    empty_deleted = 0
    for t in Topic.query.all():
        if (t.url_count or 0) <= 0 and not t.children:
            tags = PostTopicTag.query.filter_by(topic_id=t.id).count()
            scores = UrlTopicScore.query.filter_by(topic_id=t.id).count()
            if tags == 0 and scores == 0:
                print(f"  Deleting empty: '{t.name}' (parent={t.parent.name if t.parent else 'ROOT'})")
                db.session.delete(t)
                empty_deleted += 1

    db.session.commit()

    remaining = Topic.query.count()
    roots = Topic.query.filter_by(parent_id=None).count()
    print(f"\n=== Done ===")
    print(f"Merged: {total_merged} duplicates")
    print(f"Deleted: {empty_deleted} empty topics")
    print(f"Remaining: {remaining} topics ({roots} root)")
