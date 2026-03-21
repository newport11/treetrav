from datetime import datetime

from flask import jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import token_or_key_auth
from app.models import CanonicalUrl, Post, User
from app.services.canonicalization import canonicalize_url


@bp.route("/urls/<int:canonical_id>", methods=["GET"])
def get_canonical_url(canonical_id):
    """Get canonical URL with metadata, scores, and submission count."""
    canonical = CanonicalUrl.query.get_or_404(canonical_id)
    return jsonify(canonical.to_dict())


@bp.route("/urls/lookup", methods=["GET"])
def lookup_url():
    """Resolve a raw URL to its canonical form."""
    raw = request.args.get("url", "").strip()
    if not raw:
        return bad_request("must include url parameter")

    canonical_form, url_hash, domain = canonicalize_url(raw)
    existing = CanonicalUrl.query.filter_by(url_hash=url_hash).first()

    if existing:
        return jsonify({
            "found": True,
            "canonical": existing.to_dict(),
        })

    return jsonify({
        "found": False,
        "canonical_url": canonical_form,
        "url_hash": url_hash,
        "domain": domain,
    })


@bp.route("/urls/<int:canonical_id>/submissions", methods=["GET"])
def get_url_submissions(canonical_id):
    """Get all posts that reference this canonical URL."""
    canonical = CanonicalUrl.query.get_or_404(canonical_id)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)

    posts = (
        Post.query.filter_by(canonical_url_id=canonical_id)
        .order_by(Post.timestamp.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "canonical_url_id": canonical_id,
        "total": posts.total,
        "page": page,
        "posts": [p.to_dict() for p in posts.items],
    })


@bp.route("/urls/<int:canonical_id>/contributors", methods=["GET"])
def get_url_contributors(canonical_id):
    """Get agents/users who submitted this URL, with trust scores."""
    canonical = CanonicalUrl.query.get_or_404(canonical_id)
    sort = request.args.get("sort", "earliest")

    posts = Post.query.filter_by(canonical_url_id=canonical_id).all()

    # Group by user
    user_map = {}
    for post in posts:
        uid = post.user_id
        if uid not in user_map:
            user_map[uid] = {
                "first_submitted": post.timestamp,
                "submission_count": 0,
            }
        user_map[uid]["submission_count"] += 1
        if post.timestamp and (
            user_map[uid]["first_submitted"] is None
            or post.timestamp < user_map[uid]["first_submitted"]
        ):
            user_map[uid]["first_submitted"] = post.timestamp

    users = User.query.filter(User.id.in_(user_map.keys())).all()
    contributors = []
    for user in users:
        info = user_map[user.id]
        contributors.append({
            "user_id": user.id,
            "username": user.username,
            "is_agent": user.is_agent,
            "trust_score": user.trust_score if user.trust_score is not None else 0.5,
            "first_submitted": info["first_submitted"].isoformat() + "Z" if info["first_submitted"] else None,
            "submission_count": info["submission_count"],
        })

    if sort == "earliest":
        contributors.sort(key=lambda c: c["first_submitted"] or "")
    elif sort == "trust":
        contributors.sort(key=lambda c: c["trust_score"], reverse=True)

    return jsonify({
        "canonical_url_id": canonical_id,
        "contributors": contributors,
    })
