import asyncio
import urllib.parse

from flask import abort, current_app, jsonify, request, url_for

from app import db
from app.api import bp
from app.api.auth import token_auth
from app.api.errors import bad_request
from app.favicon import get_domain_from_url, get_favicon, hash_url
from app.models import Post, User
from app.openai import generate_link_summary
from app.utils import get_webpage_title, is_subpath


@bp.route("/posts/<int:id>", methods=["GET"])
@token_auth.login_required
def get_post(id):
    return jsonify(Post.query.get_or_404(id).to_dict())


@bp.route("/post_link", methods=["POST"])
@token_auth.login_required
async def post_link():
    data = request.get_json() or {}
    if "link" not in data or data["link"] == "":
        return bad_request("must include link field")
    if "text" not in data or data["text"].strip() == "":
        text = None
    else:
        text = data["text"].strip()
    if "description" not in data or data["description"].strip() == "":
        description = None
    else:
        description = data["description"].strip()
    if "folder" not in data:
        folder = "/"
    else:
        folder = data["folder"].strip()
    if not folder:
        folder = "/"

    link = urllib.parse.quote(data["link"])
    OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]
    if token_auth.current_user().inbound_shares and folder != "/":
        for share in token_auth.current_user().inbound_shares:
            sharee_folder_path = share.sharee_folder_path
            sharer_folder_path = share.sharer_folder_path
            sharer_id = share.sharer_id
            if sharee_folder_path == "/":
                path_to_check = sharer_folder_path.rstrip("/").rsplit("/", 1)[-1]
            else:
                path_to_check = (
                    sharee_folder_path
                    + "/"
                    + sharer_folder_path.rstrip("/").rsplit("/", 1)[-1]
                )
            if is_subpath(path_to_check, folder):
                sharer = User.query.filter_by(id=sharer_id).first()
                if sharer is None:
                    continue
                else:
                    new_folder = sharer_folder_path + folder[len(path_to_check) :]
                    post = Post(
                        link=link,
                        body=text,
                        description=description,
                        folder_link=new_folder.strip("/"),
                        author=sharer,
                    )
                    # If post.body is None, try to set it to the webpage title
                    if not post.body:
                        webpage_title = get_webpage_title(link)
                        if webpage_title:
                            post.body = webpage_title
                        elif OPENAI_API_KEY:
                            post.body = generate_link_summary(
                                post.link, OPENAI_API_KEY
                            ).rstrip(".")

                    favicon_file_name = await asyncio.wait_for(
                        get_favicon(post.link), 8
                    )
                    if favicon_file_name:
                        post.favicon_file_name = favicon_file_name
                    db.session.add(post)
                    db.session.commit()
                    response = jsonify(post.to_dict())
                    response.status_code = 201
                    response.headers["Location"] = url_for("api.get_post", id=post.id)
                    return response

    post = Post(
        link=link,
        body=text,
        description=description,
        folder_link=folder.strip("/") if folder != "/" else "/",
        author=token_auth.current_user(),
    )
    # If post.body is None, try to set it to the webpage title
    if not post.body:
        webpage_title = get_webpage_title(link)
        if webpage_title:
            post.body = webpage_title
        elif OPENAI_API_KEY:
            post.body = generate_link_summary(post.link, OPENAI_API_KEY).rstrip(".")

    favicon_file_name = await asyncio.wait_for(get_favicon(post.link), 8)
    if favicon_file_name:
        post.favicon_file_name = favicon_file_name
    db.session.add(post)
    db.session.commit()
    response = jsonify(post.to_dict())
    response.status_code = 201
    response.headers["Location"] = url_for("api.get_post", id=post.id)
    return response


@bp.route("/post_multiple_links", methods=["POST"])
@token_auth.login_required
async def post_multiple_links():
    data = request.get_json() or {}
    if "links" not in data or data["links"] == "":
        return bad_request("must include link field")
    if "text" not in data or data["text"].strip() == "":
        text = None
    else:
        text = data["text"].strip()
    if "description" not in data or data["description"].strip() == "":
        description = None
    else:
        description = data["description"].strip()
    if "folder" not in data:
        folder = None
    else:
        folder = data["folder"]

    tabs = data["links"]
    successful_count = 0
    OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]

    for tab in tabs:
        try:
            link = urllib.parse.quote(tab["url"])
            post = Post(
                link=link,
                body=text,
                description=description,
                folder_link=folder.strip().strip("/") if folder else "/",
                author=token_auth.current_user(),
            )
            # If post.body is None, try to set it to the webpage title
            if not post.body:
                webpage_title = get_webpage_title(link)
                if webpage_title:
                    post.body = webpage_title
                elif OPENAI_API_KEY:
                    post.body = generate_link_summary(post.link, OPENAI_API_KEY).rstrip(
                        "."
                    )

            favicon_file_name = await asyncio.wait_for(get_favicon(post.link), 8)
            if favicon_file_name:
                post.favicon_file_name = favicon_file_name
            db.session.add(post)
            db.session.commit()
            successful_count += 1
        except:
            pass
    if successful_count > 0:
        return jsonify({"Success Count": successful_count, "status": 200})
    else:
        return jsonify(
            {
                "Success Count": successful_count,
                "status": 401,
                "error": "Links were not posted successfully",
            }
        )


@bp.route("/posts/get_num_posts", methods=["GET"])
def get_num_posts():
    data = request.get_json() or {}
    if data["api_key"] != current_app.config["ADMIN_API_KEY"]:
        abort(403)
    return jsonify({"num_posts": Post.query.count()})


""" 
@bp.route('/posts/update_favicons/<int:id>', methods=['POST'])
async def update_favicons(id):
    data = request.get_json() or {}
    if data['api_key'] != current_app.config["ADMIN_API_KEY"]:
        abort(403)
    user = User.query.get_or_404(id)
    posts = user.posts
    count = 0
    for post in posts:
        favicon_file_name = await asyncio.wait_for(get_favicon(post.link), 8)
        if favicon_file_name and favicon_file_name != "leaf.png":
            post.favicon_file_name = favicon_file_name
            count += 1
    db.session.commit()
    return jsonify({"favicons updated": count})


@bp.route('/posts/update_favicon_names/<int:id>', methods=['POST'])
def update_favicons(id):
    data = request.get_json() or {}
    if data['api_key'] != current_app.config["ADMIN_API_KEY"]:
        abort(403)
    user = User.query.get_or_404(id)
    posts = user.posts
    count = 0
    for post in posts:
        if post.favicon_file_name and post.favicon_file_name != "leaf.png":
            url = urllib.parse.unquote(post.link)
            domain = get_domain_from_url(url)
            hashed_domain = hash_url(domain)
            if  post.favicon_file_name != f"{hashed_domain}.png":
                post.favicon_file_name = f"{hashed_domain}.png"
                count += 1
    db.session.commit()
    return jsonify({"favicons updated": count})

"""
