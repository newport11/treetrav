# app/main/services.py

import asyncio
import urllib.parse

from flask import current_app
from flask_sqlalchemy import Pagination

from app import db
from app.favicon import get_favicon
from app.main.forms import PostForm
from app.models import Post, User
from app.openai import generate_link_summary
from app.utils import get_webpage_title


async def create_post(form: PostForm, current_user: User):
    """Create a new post based on the submitted form data."""
    OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]
    folder_path = form.post_folder.data.strip()
    folder_path = (
        "/" if not folder_path or folder_path == "/" else folder_path.strip("/")
    )
    post = Post(
        link=urllib.parse.quote(form.post_link.data),
        body=form.post_body.data,
        description=form.post_description.data.strip(),
        folder_link=folder_path,
        author=current_user,
    )

    if not post.body:
        webpage_title = get_webpage_title(form.post_link.data)
        if webpage_title:
            post.body = webpage_title
        elif OPENAI_API_KEY:
            post.body = generate_link_summary(post.link, OPENAI_API_KEY).rstrip(".")

    favicon_file_name = await asyncio.wait_for(get_favicon(post.link), 8)
    if favicon_file_name:
        post.favicon_file_name = favicon_file_name

    return post, True


async def get_posts_query(
    route_type: str, current_user: User, search_query: str, page: int
) -> Pagination:
    """Retrieve a paginated query of posts based on the route type and search query."""
    if route_type == "feed":
        base_query = current_user.followed_posts()
    else:  # discover
        base_query = (
            db.session.query(Post).join(User).filter(User.private_mode == False)
        )

    if search_query:
        base_query = base_query.filter(
            db.or_(
                Post.body.ilike(f"%{search_query}%"),
                Post.link.ilike(f"%{search_query}%"),
                Post.description.ilike(f"%{search_query}%"),
            )
        )

    return base_query.order_by(Post.timestamp.desc()).paginate(
        page=page,
        per_page=current_app.config["POSTS_PER_PAGE"],
        error_out=False,
    )
