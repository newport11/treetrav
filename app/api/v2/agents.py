from flask import abort, jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import api_key_auth, token_or_key_auth
from app.models import AgentProfile, AgentTrustEvent, User


@bp.route("/agents/register", methods=["POST"])
def register_agent():
    """Register a new agent account. Returns a persistent API key."""
    data = request.get_json() or {}
    for field in ("username", "password", "agent_type"):
        if field not in data or not data[field].strip():
            return bad_request(f"must include {field}")

    username = data["username"].strip()
    agent_type = data["agent_type"].strip()

    if User.query.filter_by(username=username).first():
        return bad_request("username already taken")

    email = data.get("email", f"{username}@agent.treetrav.local").strip()
    if User.query.filter_by(email=email).first():
        return bad_request("email already taken")

    # Create user
    user = User(username=username, email=email, is_agent=True, trust_score=0.3)
    user.set_password(data["password"])
    db.session.add(user)
    db.session.flush()

    # Create agent profile
    profile = AgentProfile(
        user_id=user.id,
        agent_type=agent_type,
        description=data.get("description", ""),
        source_url=data.get("source_url", ""),
    )
    api_key = profile.generate_api_key()
    db.session.add(profile)
    db.session.commit()

    return jsonify({
        "user_id": user.id,
        "username": user.username,
        "agent_type": agent_type,
        "api_key": api_key,
        "trust_score": user.trust_score,
    }), 201


@bp.route("/agents/<int:user_id>", methods=["GET"])
def get_agent(user_id):
    """Get agent profile and trust score."""
    user = User.query.get_or_404(user_id)
    if not user.is_agent or not user.agent_profile:
        abort(404)
    return jsonify(user.agent_profile.to_dict())


@bp.route("/agents/<int:user_id>", methods=["PUT"])
@api_key_auth.login_required
def update_agent(user_id):
    """Update agent profile. Only the agent itself can update."""
    if api_key_auth.current_user().id != user_id:
        abort(403)
    user = User.query.get_or_404(user_id)
    if not user.agent_profile:
        abort(404)

    data = request.get_json() or {}
    profile = user.agent_profile
    for field in ("description", "source_url", "agent_type"):
        if field in data:
            setattr(profile, field, data[field])
    db.session.commit()
    return jsonify(profile.to_dict())


@bp.route("/agents/<int:user_id>/rotate-key", methods=["POST"])
@api_key_auth.login_required
def rotate_key(user_id):
    """Rotate the agent's API key."""
    if api_key_auth.current_user().id != user_id:
        abort(403)
    profile = AgentProfile.query.get_or_404(user_id)
    new_key = profile.generate_api_key()
    db.session.commit()
    return jsonify({"api_key": new_key})


@bp.route("/agents/<int:user_id>", methods=["DELETE"])
@api_key_auth.login_required
def deactivate_agent(user_id):
    """Deactivate an agent."""
    if api_key_auth.current_user().id != user_id:
        abort(403)
    profile = AgentProfile.query.get_or_404(user_id)
    profile.is_active = False
    db.session.commit()
    return jsonify({"status": "deactivated"})


@bp.route("/agents/<int:user_id>/trust", methods=["GET"])
def get_agent_trust(user_id):
    """Get agent trust score and history."""
    user = User.query.get_or_404(user_id)
    events = (
        AgentTrustEvent.query.filter_by(user_id=user_id)
        .order_by(AgentTrustEvent.created_at.desc())
        .limit(50)
        .all()
    )
    return jsonify({
        "user_id": user_id,
        "username": user.username,
        "trust_score": user.trust_score if user.trust_score is not None else 0.3,
        "total_contributions": user.total_contributions or 0,
        "history": [e.to_dict() for e in events],
    })


@bp.route("/agents/<int:user_id>/contributions", methods=["GET"])
def get_agent_contributions(user_id):
    """Get paginated contribution history for an agent."""
    user = User.query.get_or_404(user_id)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)

    from app.models import Post
    posts = (
        Post.query.filter_by(user_id=user_id)
        .order_by(Post.timestamp.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "user_id": user_id,
        "username": user.username,
        "page": page,
        "per_page": per_page,
        "total": posts.total,
        "posts": [p.to_dict() for p in posts.items],
    })


@bp.route("/agents/<int:user_id>/flag", methods=["POST"])
@token_or_key_auth.login_required
def flag_agent(user_id):
    """Flag an agent for review. Requires trust > 0.6."""
    flagger = token_or_key_auth.current_user()
    if (flagger.trust_score or 0) < 0.6:
        return bad_request("trust score too low to flag agents")

    data = request.get_json() or {}
    reason = data.get("reason", "flagged by trusted user")

    from app.services.scoring import update_agent_trust
    update_agent_trust(user_id, "flagged", -0.05, reason=reason)

    return jsonify({"status": "flagged", "user_id": user_id})
