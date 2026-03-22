import re

from app import db
from app.models import Topic, TopicAlias


def slugify(name):
    """Convert a topic name to a URL-friendly slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def compute_path(topic):
    """Compute the materialized path for a topic."""
    parts = []
    current = topic
    while current:
        parts.insert(0, current.slug)
        current = current.parent
    return "/".join(parts)


def create_topic(name, description=None, parent_id=None):
    """Create a new topic in the taxonomy. Case-insensitive matching."""
    slug = slugify(name)

    existing = Topic.query.filter(
        db.or_(
            db.func.lower(Topic.name) == name.lower(),
            db.func.lower(Topic.slug) == slug.lower(),
        )
    ).first()
    if existing:
        return existing, False

    topic = Topic(name=name, slug=slug, description=description, parent_id=parent_id)

    if parent_id:
        parent = Topic.query.get(parent_id)
        if parent:
            topic.depth = parent.depth + 1
    else:
        topic.depth = 0

    db.session.add(topic)
    db.session.flush()

    topic.path = compute_path(topic)
    db.session.commit()
    return topic, True


def merge_topics(source_id, target_id):
    """Merge source topic into target. Moves all tags and creates an alias."""
    source = Topic.query.get(source_id)
    target = Topic.query.get(target_id)
    if not source or not target:
        return None

    from app.models import PostTopicTag, UrlTopicScore, UrlPropagation, TopicSubscription

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
            existing.vote_count += score.vote_count
            existing.combined_score = max(existing.combined_score, score.combined_score)
            db.session.delete(score)
        else:
            score.topic_id = target_id

    # Move propagations
    UrlPropagation.query.filter_by(topic_id=source_id).update(
        {"topic_id": target_id}, synchronize_session=False
    )

    # Move subscriptions
    for sub in TopicSubscription.query.filter_by(topic_id=source_id).all():
        existing = TopicSubscription.query.filter_by(
            user_id=sub.user_id, topic_id=target_id
        ).first()
        if existing:
            db.session.delete(sub)
        else:
            sub.topic_id = target_id

    # Move children
    for child in source.children:
        child.parent_id = target_id
        child.path = compute_path(child)

    # Create alias
    alias = TopicAlias(alias_name=source.name, topic_id=target_id)
    db.session.add(alias)

    # Also preserve existing aliases
    TopicAlias.query.filter_by(topic_id=source_id).update(
        {"topic_id": target_id}, synchronize_session=False
    )

    # Deactivate source
    source.is_active = False
    target.url_count += source.url_count

    db.session.commit()
    return target


def split_topic(topic_id, new_names):
    """Split a topic into multiple subtopics."""
    parent = Topic.query.get(topic_id)
    if not parent:
        return []

    new_topics = []
    for name in new_names:
        topic, created = create_topic(name, parent_id=topic_id)
        new_topics.append(topic)

    return new_topics


def seed_default_topics():
    """Seed the initial topic taxonomy."""
    topics = [
        ("Technology", "Technology and computing", [
            ("Artificial Intelligence", "AI, ML, deep learning"),
            ("Web Development", "Frontend, backend, web technologies"),
            ("Cybersecurity", "Security, privacy, cryptography"),
            ("Cloud Computing", "AWS, GCP, Azure, infrastructure"),
            ("Mobile Development", "iOS, Android, mobile apps"),
            ("DevOps", "CI/CD, containers, orchestration"),
            ("Programming Languages", "Languages, compilers, tooling"),
            ("Databases", "SQL, NoSQL, data storage"),
        ]),
        ("Science", "Scientific research and discovery", [
            ("Biology", "Life sciences, genetics, ecology"),
            ("Physics", "Physics research and discoveries"),
            ("Chemistry", "Chemical sciences"),
            ("Neuroscience", "Brain science, cognitive research"),
            ("Climate Science", "Climate change, environmental science"),
        ]),
        ("Business", "Business and entrepreneurship", [
            ("Startups", "Startup ecosystem, fundraising"),
            ("Finance", "Markets, investing, fintech"),
            ("Marketing", "Digital marketing, growth"),
            ("Enterprise Software", "B2B, SaaS, enterprise tech"),
        ]),
        ("Health", "Health and medicine", [
            ("Medicine", "Medical research and practice"),
            ("Mental Health", "Psychology, therapy, wellness"),
            ("Biotech", "Biotechnology, pharma"),
        ]),
        ("Policy", "Government and regulation", [
            ("Tech Policy", "Tech regulation, antitrust, privacy law"),
            ("Regulatory Policy", "Government regulations"),
        ]),
        ("Culture", "Culture and society", [
            ("Media", "News, journalism, media industry"),
            ("Education", "Learning, academia, edtech"),
        ]),
    ]

    created = []
    for parent_name, parent_desc, children in topics:
        parent, was_created = create_topic(parent_name, parent_desc)
        if was_created:
            created.append(parent)
        for child_name, child_desc in children:
            child, was_created = create_topic(child_name, child_desc, parent_id=parent.id)
            if was_created:
                created.append(child)

    return created
