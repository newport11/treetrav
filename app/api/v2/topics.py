from flask import abort, jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import token_or_key_auth
from app.models import Topic, TopicAlias
from app.services.taxonomy import create_topic, merge_topics, seed_default_topics, slugify, split_topic


@bp.route("/topics", methods=["GET"])
def list_topics():
    """List all topics. Use ?format=tree for hierarchical view, ?format=flat for flat list."""
    fmt = request.args.get("format", "tree")
    active_only = request.args.get("active", "true").lower() == "true"

    query = Topic.query
    if active_only:
        query = query.filter_by(is_active=True)

    if fmt == "flat":
        topics = query.order_by(Topic.path).all()
        return jsonify({"topics": [t.to_dict() for t in topics]})

    # Tree format — only root topics, with children nested
    roots = query.filter_by(parent_id=None).order_by(Topic.name).all()
    return jsonify({"topics": [t.to_dict(include_children=True) for t in roots]})


@bp.route("/topics/<int:topic_id>", methods=["GET"])
def get_topic(topic_id):
    """Get a single topic with children and stats."""
    topic = Topic.query.get_or_404(topic_id)
    data = topic.to_dict(include_children=True)
    return jsonify(data)


@bp.route("/topics", methods=["POST"])
@token_or_key_auth.login_required
def create_new_topic():
    """Propose a new topic. Requires trust > 0.6 for agents."""
    user = token_or_key_auth.current_user()
    if user.is_agent and (user.trust_score or 0) < 0.6:
        return bad_request("trust score too low to create topics (need > 0.6)")

    data = request.get_json() or {}
    if "name" not in data or not data["name"].strip():
        return bad_request("must include topic name")

    topic, created = create_topic(
        name=data["name"].strip(),
        description=data.get("description", ""),
        parent_id=data.get("parent_id"),
    )

    if not created:
        return jsonify({"message": "topic already exists", "topic": topic.to_dict()}), 200

    return jsonify(topic.to_dict()), 201


@bp.route("/topics/<int:topic_id>", methods=["PUT"])
@token_or_key_auth.login_required
def update_topic(topic_id):
    """Update topic metadata."""
    topic = Topic.query.get_or_404(topic_id)
    data = request.get_json() or {}

    if "name" in data:
        topic.name = data["name"].strip()
        topic.slug = slugify(topic.name)
    if "description" in data:
        topic.description = data["description"]
    if "is_active" in data:
        topic.is_active = data["is_active"]

    db.session.commit()
    return jsonify(topic.to_dict())


@bp.route("/topics/<int:topic_id>/merge", methods=["POST"])
@token_or_key_auth.login_required
def merge_topic(topic_id):
    """Merge this topic into another. Requires trust > 0.8 or is not agent."""
    user = token_or_key_auth.current_user()
    if user.is_agent and (user.trust_score or 0) < 0.8:
        return bad_request("trust score too low to merge topics")

    data = request.get_json() or {}
    target_id = data.get("target_id")
    if not target_id:
        return bad_request("must include target_id")

    result = merge_topics(topic_id, target_id)
    if not result:
        return bad_request("merge failed — invalid topic IDs")

    return jsonify({"message": "merged", "target": result.to_dict()})


@bp.route("/topics/<int:topic_id>/split", methods=["POST"])
@token_or_key_auth.login_required
def split_topic_endpoint(topic_id):
    """Split a topic into subtopics."""
    user = token_or_key_auth.current_user()
    if user.is_agent and (user.trust_score or 0) < 0.8:
        return bad_request("trust score too low to split topics")

    data = request.get_json() or {}
    new_names = data.get("names", [])
    if not new_names:
        return bad_request("must include names list")

    new_topics = split_topic(topic_id, new_names)
    return jsonify({"parent_id": topic_id, "new_topics": [t.to_dict() for t in new_topics]})


@bp.route("/topics/search", methods=["GET"])
def search_topics():
    """Fuzzy search for topics by name or alias."""
    q = request.args.get("q", "").strip()
    if not q:
        return bad_request("must include q parameter")

    pattern = f"%{q}%"
    # Search topics by name
    topics = Topic.query.filter(
        db.or_(
            Topic.name.ilike(pattern),
            Topic.slug.ilike(pattern),
            Topic.description.ilike(pattern),
        ),
        Topic.is_active == True,
    ).limit(20).all()

    # Also search aliases
    alias_topics = (
        db.session.query(Topic)
        .join(TopicAlias, TopicAlias.topic_id == Topic.id)
        .filter(TopicAlias.alias_name.ilike(pattern), Topic.is_active == True)
        .limit(10)
        .all()
    )

    # Deduplicate
    seen = set()
    results = []
    for t in topics + alias_topics:
        if t.id not in seen:
            seen.add(t.id)
            results.append(t.to_dict())

    return jsonify({"query": q, "results": results})


@bp.route("/topics/<int:topic_id>/ancestors", methods=["GET"])
def get_topic_ancestors(topic_id):
    """Get the path from root to this topic."""
    topic = Topic.query.get_or_404(topic_id)
    ancestors = []
    current = topic
    while current:
        ancestors.insert(0, current.to_dict())
        current = current.parent
    return jsonify({"ancestors": ancestors})


@bp.route("/topics/seed", methods=["POST"])
@token_or_key_auth.login_required
def seed_topics():
    """Seed default topics (admin action)."""
    created = seed_default_topics()
    return jsonify({"created": len(created), "topics": [t.to_dict() for t in created]})
