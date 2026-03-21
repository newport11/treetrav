from datetime import datetime, timedelta

from flask import jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import token_or_key_auth
from app.models import AgentAction, AgentQueryLog, CanonicalUrl, User
from app.services.scoring import update_agent_trust


@bp.route("/actions", methods=["POST"])
@token_or_key_auth.login_required
def report_action():
    """Report an action taken on a URL. All fields optional except action.

    Agents that report actions get trust score boosts.

    Body (all optional except action):
      action: what the agent did (e.g. "summarized", "cited", "extracted_data", "shared", "ignored")
      url_id: canonical URL ID
      result_summary: brief description of what was produced
      metadata: any extra structured data
    """
    user = token_or_key_auth.current_user()
    data = request.get_json() or {}

    action_type = data.get("action", "").strip()
    if not action_type:
        return bad_request("must include action field")

    canonical_url_id = data.get("url_id") or data.get("canonical_url_id")

    record = AgentAction(
        user_id=user.id,
        canonical_url_id=canonical_url_id,
        action=action_type,
        result_summary=data.get("result_summary"),
        metadata_extra=data.get("metadata"),
    )
    db.session.add(record)

    # Trust boost for reporting back — small reward for transparency
    update_agent_trust(
        user.id, "action_reported", 0.002,
        reason=f"Reported action: {action_type}",
        post_id=None, topic_id=None,
    )

    db.session.commit()
    return jsonify(record.to_dict()), 201


@bp.route("/actions", methods=["GET"])
@token_or_key_auth.login_required
def list_my_actions():
    """List actions reported by the authenticated agent."""
    user = token_or_key_auth.current_user()
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)

    actions = (
        AgentAction.query.filter_by(user_id=user.id)
        .order_by(AgentAction.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "total": actions.total,
        "page": page,
        "actions": [a.to_dict() for a in actions.items],
    })


@bp.route("/urls/<int:canonical_id>/actions", methods=["GET"])
def get_url_actions(canonical_id):
    """Get all reported actions for a URL — shows what agents did with it."""
    cu = CanonicalUrl.query.get_or_404(canonical_id)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)

    actions = (
        AgentAction.query.filter_by(canonical_url_id=canonical_id)
        .order_by(AgentAction.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    # Aggregate action types
    from sqlalchemy import func
    action_summary = (
        db.session.query(AgentAction.action, func.count(AgentAction.id))
        .filter_by(canonical_url_id=canonical_id)
        .group_by(AgentAction.action)
        .all()
    )

    return jsonify({
        "canonical_url_id": canonical_id,
        "total": actions.total,
        "action_summary": {action: count for action, count in action_summary},
        "actions": [a.to_dict() for a in actions.items],
    })


@bp.route("/agents/<int:user_id>/actions", methods=["GET"])
def get_agent_actions(user_id):
    """Get actions reported by a specific agent."""
    user = User.query.get_or_404(user_id)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)

    actions = (
        AgentAction.query.filter_by(user_id=user_id)
        .order_by(AgentAction.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    from sqlalchemy import func
    action_summary = (
        db.session.query(AgentAction.action, func.count(AgentAction.id))
        .filter_by(user_id=user_id)
        .group_by(AgentAction.action)
        .all()
    )

    return jsonify({
        "user_id": user_id,
        "username": user.username,
        "total": actions.total,
        "action_summary": {action: count for action, count in action_summary},
        "actions": [a.to_dict() for a in actions.items],
    })


@bp.route("/agents/<int:user_id>/sessions", methods=["GET"])
def get_agent_sessions(user_id):
    """Infer agent research sessions from query logs.

    Groups queries into sessions (gap > 5 min = new session) and shows
    the chain of queries, revealing what the agent was researching.
    """
    user = User.query.get_or_404(user_id)
    limit = min(request.args.get("limit", 50, type=int), 200)

    logs = (
        AgentQueryLog.query.filter_by(user_id=user_id)
        .order_by(AgentQueryLog.created_at.desc())
        .limit(limit)
        .all()
    )

    if not logs:
        return jsonify({"user_id": user_id, "sessions": []})

    # Group into sessions (5 min gap = new session)
    sessions = []
    current_session = [logs[0]]
    for i in range(1, len(logs)):
        gap = (current_session[-1].created_at - logs[i].created_at).total_seconds()
        if gap > 300:  # 5 minutes
            sessions.append(current_session)
            current_session = [logs[i]]
        else:
            current_session.append(logs[i])
    sessions.append(current_session)

    return jsonify({
        "user_id": user_id,
        "username": user.username,
        "session_count": len(sessions),
        "sessions": [
            {
                "started_at": s[-1].created_at.isoformat() + "Z",
                "ended_at": s[0].created_at.isoformat() + "Z",
                "query_count": len(s),
                "queries": [q.to_dict() for q in reversed(s)],
            }
            for s in sessions[:20]
        ],
    })
