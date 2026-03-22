# app/main/services.py

import asyncio
import urllib.parse
from datetime import datetime

from flask import current_app
from flask_sqlalchemy import Pagination

from app import db
from app.favicon import get_favicon
from app.main.forms import PostForm
from app.models import CanonicalUrl, Post, User
from app.openai import generate_link_summary
from app.services.canonicalization import canonicalize_url
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

    # Canonicalize URL
    try:
        canonical_form, url_hash, domain = canonicalize_url(form.post_link.data)
        cu = CanonicalUrl.query.filter_by(url_hash=url_hash).first()
        if cu:
            cu.submission_count = (cu.submission_count or 0) + 1
            cu.last_seen = datetime.utcnow()
        else:
            cu = CanonicalUrl(
                canonical_url=canonical_form, url_hash=url_hash,
                domain=domain, submission_count=1,
            )
            db.session.add(cu)
            db.session.flush()
        post.canonical_url_id = cu.id
        post.content_hash = url_hash
        current_user.total_contributions = (current_user.total_contributions or 0) + 1
    except Exception:
        pass

    # Auto-tag from folder path
    try:
        from app.api.posts import _auto_tag_from_folder
        db.session.add(post)
        db.session.flush()
        _auto_tag_from_folder(post)
    except Exception:
        pass

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
