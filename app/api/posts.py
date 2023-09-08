from flask import current_app, jsonify, request, url_for, abort
from app import db
from app.favicon import get_favicon
from app.models import Post
from app.api import bp
from app.api.auth import token_auth
from app.api.errors import bad_request


@bp.route('/posts/<int:id>', methods=['GET'])
@token_auth.login_required
def get_post(id):
    return jsonify(Post.query.get_or_404(id).to_dict())


@bp.route('/post_link', methods=['POST'])
@token_auth.login_required
def post_link():
    data = request.get_json() or {}
    if 'link' not in data or data['link'] == "":
        return bad_request('must include link field')
    if 'text' not in data or data['text'] == "":
        text = data['link']
    else:
        text = data['text']
    if 'folder' not in data:
        folder=None
    else:
        folder=data['folder']
    link = data['link']
    post = Post(link=link, body=text, folder_link=folder.strip().strip("/") if folder else "/", author=token_auth.current_user())
    favicon_file_name = get_favicon(post.link)
    if favicon_file_name:
        post.favicon_file_name = favicon_file_name
    db.session.add(post)
    db.session.commit()
    response = jsonify(post.to_dict())
    response.status_code = 201
    response.headers['Location'] = url_for('api.get_post', id=post.id)
    return response



@bp.route('/posts/get_num_posts', methods=['GET'])
def get_num_posts():
    data = request.get_json() or {}
    if data['api_key'] != current_app.config["ADMIN_API_KEY"]:
        abort(403)
    return jsonify({"num_posts": Post.query.count()})
