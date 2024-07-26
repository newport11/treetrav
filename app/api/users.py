from flask import abort, current_app, jsonify, request, url_for

from app import db
from app.api import bp
from app.api.auth import token_auth
from app.api.errors import bad_request
from app.favicon import get_favicon
from app.models import Post, User


@bp.route("/users/<int:id>", methods=["GET"])
@token_auth.login_required
def get_user(id):
    if token_auth.current_user().id != id:
        abort(403)
    return jsonify(User.query.get_or_404(id).to_dict())


@bp.route("/users/get_current_username", methods=["GET"])
@token_auth.login_required
def get_current_username():
    current_app.logger.info(token_auth.current_user().username)
    return jsonify(User.query.get_or_404(token_auth.current_user().id).to_dict())


@bp.route("/users", methods=["GET"])
@token_auth.login_required
def get_users():
    if data["api_key"] != current_app.config["ADMIN_API_KEY"]:
        abort(403)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 10, type=int), 100)
    data = User.to_collection_dict(User.query, page, per_page, "api.get_users")
    return jsonify(data)


@bp.route("/users/<int:id>/followers", methods=["GET"])
@token_auth.login_required
def get_followers(id):
    if token_auth.current_user().id != id:
        abort(403)
    user = User.query.get_or_404(id)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 10, type=int), 100)
    data = User.to_collection_dict(
        user.followers, page, per_page, "api.get_followers", id=id
    )
    return jsonify(data)


@bp.route("/users/<int:id>/followed", methods=["GET"])
@token_auth.login_required
def get_followed(id):
    if token_auth.current_user().id != id:
        abort(403)
    user = User.query.get_or_404(id)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 10, type=int), 100)
    data = User.to_collection_dict(
        user.followed, page, per_page, "api.get_followed", id=id
    )
    return jsonify(data)


@bp.route("/users", methods=["POST"])
def create_user():
    data = request.get_json() or {}
    if "username" not in data or "email" not in data or "password" not in data:
        return bad_request("must include username, email and password fields")
    if User.query.filter_by(username=data["username"].strip()).first():
        return bad_request("please use a different username")
    if User.query.filter_by(email=data["email"].strip()).first():
        return bad_request("please use a different email address")
    user = User()
    user.from_dict(data, new_user=True)
    db.session.add(user)
    db.session.commit()
    response = jsonify(user.to_dict())
    response.status_code = 201
    response.headers["Location"] = url_for("api.get_user", id=user.id)
    return response


@bp.route("/users/<int:id>", methods=["PUT"])
@token_auth.login_required
def update_user(id):
    if token_auth.current_user().id != id:
        abort(403)
    user = User.query.get_or_404(id)
    data = request.get_json() or {}
    if (
        "username" in data
        and data["username"] != user.username
        and User.query.filter_by(username=data["username"].strip()).first()
    ):
        return bad_request("please use a different username")
    if (
        "email" in data
        and data["email"] != user.email
        and User.query.filter_by(email=data["email"].strip()).first()
    ):
        return bad_request("please use a different email address")
    user.from_dict(data, new_user=False)
    db.session.commit()
    return jsonify(user.to_dict())


@bp.route("/users/verify/<int:id>", methods=["PUT"])
def verify_user(id):
    data = request.get_json() or {}
    if "bool" not in data or "api_key" not in data:
        return bad_request("must include bool and api_key")
    bool_str = data["bool"]
    if bool_str == "True":
        verify = True
    elif bool_str == "False":
        verify = False
    else:
        return bad_request("bool must be True or False")
    if data["api_key"] != current_app.config["ADMIN_API_KEY"]:
        abort(403)
    user = User.query.get_or_404(id)
    user.update_verification(verify)
    db.session.commit()
    return jsonify(user.to_dict())


@bp.route("/users/get_num_users", methods=["GET"])
def get_num_users():
    data = request.get_json() or {}
    if data["api_key"] != current_app.config["ADMIN_API_KEY"]:
        abort(403)
    return jsonify({"num_users": User.query.count()})


@bp.route("/users/get_num_verified_users", methods=["GET"])
def get_num_verified_users():
    data = request.get_json() or {}
    if data["api_key"] != current_app.config["ADMIN_API_KEY"]:
        abort(403)
    return jsonify({"num_verified_users": User.query.filter_by(verified=True).count()})
