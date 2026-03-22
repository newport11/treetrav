from flask import abort, jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import token_or_key_auth
from app.models import Post, User


# ==================== FOLLOW ====================


@bp.route("/follow/<username>", methods=["POST"])
@token_or_key_auth.login_required
def api_follow(username):
    """Follow a user. If they're private, sends a follow request instead."""
    user = token_or_key_auth.current_user()
    target = User.query.filter_by(username=username).first_or_404()
    if user.id == target.id:
        return bad_request("cannot follow yourself")
    if user.is_following(target):
        return jsonify({"status": "already_following", "username": username})

    if target.private_mode:
        if not user.is_requested(target):
            user.request_follow(target)
            db.session.commit()
        return jsonify({"status": "follow_requested", "username": username})

    user.follow(target)
    db.session.commit()
    return jsonify({"status": "following", "username": username})


@bp.route("/unfollow/<username>", methods=["POST"])
@token_or_key_auth.login_required
def api_unfollow(username):
    """Unfollow a user."""
    user = token_or_key_auth.current_user()
    target = User.query.filter_by(username=username).first_or_404()
    if user.id == target.id:
        return bad_request("cannot unfollow yourself")

    user.unfollow(target)
    user.unrequest_follow(target)
    db.session.commit()
    return jsonify({"status": "unfollowed", "username": username})


@bp.route("/following", methods=["GET"])
@token_or_key_auth.login_required
def api_my_following():
    """List users you are following."""
    user = token_or_key_auth.current_user()
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)
    following = user.followed.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "total": following.total,
        "page": page,
        "following": [
            {"user_id": u.id, "username": u.username, "is_agent": u.is_agent,
             "trust_score": u.trust_score}
            for u in following.items
        ],
    })


@bp.route("/followers", methods=["GET"])
@token_or_key_auth.login_required
def api_my_followers():
    """List your followers."""
    user = token_or_key_auth.current_user()
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)
    followers = user.followers.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "total": followers.total,
        "page": page,
        "followers": [
            {"user_id": u.id, "username": u.username, "is_agent": u.is_agent,
             "trust_score": u.trust_score}
            for u in followers.items
        ],
    })


@bp.route("/users/<username>/following", methods=["GET"])
def api_user_following(username):
    """List who a user is following (public)."""
    user = User.query.filter_by(username=username).first_or_404()
    if user.private_mode:
        abort(403)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)
    following = user.followed.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "username": username,
        "total": following.total,
        "page": page,
        "following": [
            {"user_id": u.id, "username": u.username, "is_agent": u.is_agent,
             "trust_score": u.trust_score}
            for u in following.items
        ],
    })


@bp.route("/users/<username>/followers", methods=["GET"])
def api_user_followers(username):
    """List a user's followers (public)."""
    user = User.query.filter_by(username=username).first_or_404()
    if user.private_mode:
        abort(403)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)
    followers = user.followers.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "username": username,
        "total": followers.total,
        "page": page,
        "followers": [
            {"user_id": u.id, "username": u.username, "is_agent": u.is_agent,
             "trust_score": u.trust_score}
            for u in followers.items
        ],
    })


# ==================== FAVORITES ====================


@bp.route("/favorite/<int:post_id>", methods=["POST"])
@token_or_key_auth.login_required
def api_favorite(post_id):
    """Favorite a post."""
    user = token_or_key_auth.current_user()
    post = Post.query.get_or_404(post_id)
    user.favorite(post)
    db.session.commit()
    return jsonify({"status": "favorited", "post_id": post_id})


@bp.route("/unfavorite/<int:post_id>", methods=["POST"])
@token_or_key_auth.login_required
def api_unfavorite(post_id):
    """Unfavorite a post."""
    user = token_or_key_auth.current_user()
    post = Post.query.get_or_404(post_id)
    user.unfavorite(post)
    db.session.commit()
    return jsonify({"status": "unfavorited", "post_id": post_id})


@bp.route("/favorites", methods=["GET"])
@token_or_key_auth.login_required
def api_my_favorites():
    """List your favorited posts."""
    user = token_or_key_auth.current_user()
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)
    favorites = user.favorite_posts().paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "total": favorites.total,
        "page": page,
        "favorites": [p.to_dict() for p in favorites.items],
    })


@bp.route("/users/<username>/favorites", methods=["GET"])
def api_user_favorites(username):
    """List a user's favorited posts (public)."""
    user = User.query.filter_by(username=username).first_or_404()
    if user.private_mode:
        abort(403)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)
    favorites = user.favorite_posts().paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "username": username,
        "total": favorites.total,
        "page": page,
        "favorites": [p.to_dict() for p in favorites.items],
    })
