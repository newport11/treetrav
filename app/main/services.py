# app/main/services.py

import asyncio
import os
import urllib.parse
from typing import Tuple

from flask import current_app, flash
from flask_babel import _
from flask_sqlalchemy import Pagination

from app import db
from app.constants import POST_PICS_PATH
from app.favicon import get_favicon
from app.main.forms import PostForm
from app.models import Post, PostPic, User
from app.openai import generate_link_summary
from app.utils import get_webpage_title, image_preprocessing, top_crop


async def create_post(
    form: PostForm, current_user: User
) -> Tuple[Post | PostPic, bool]:
    """
    Create a new post or post with picture based on the submitted form data.

    This function handles the creation of a new post, including processing of
    attached images, generating post body if not provided, and saving favicon.

    Args:
        form (PostForm): The submitted form containing post data.
        current_user (User): The current authenticated user creating the post.

    Returns:
        Tuple[Post | PostPic, bool]: A tuple containing the created post
        (either Post or PostPic) and a boolean indicating whether it's a regular
        post (True) or a post with picture (False).

    Raises:
        Exception: If there's an error in uploading or processing the image.
    """
    OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]
    folder_path = form.post_folder.data.strip()
    folder_path = (
        "/" if not folder_path or folder_path == "/" else folder_path.strip("/")
    )
    post_pic = form.post_pic.data
    if post_pic and post_pic != "":
        try:
            img = image_preprocessing(post_pic)

            # Center crop, resize, and compress the image to 155x155
            resized_picture = top_crop(img, (285, 285))
            post = PostPic(
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
                    post.body = generate_link_summary(post.link, OPENAI_API_KEY).rstrip(
                        "."
                    )
            db.session.add(post)
            db.session.commit()
            post_pic_filename = f"{current_user.id}_{post.id}"
            resized_picture.save(
                os.path.join(POST_PICS_PATH, f"{post_pic_filename}.jpg"),
                "JPEG",
            )
            return post, False
        except Exception as e:
            current_app.logger.error(f"Exception occurred. {e}")
            flash(_("Error in uploading image. Please try again"), "error")
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
    """
    Retrieve a paginated query of posts based on the route type and search query.

    This function constructs a query to fetch posts either from the user's feed
    or from all public posts (discover), applies search filtering if a query is
    provided, and returns a paginated result.

    Args:
        route_type (str): The type of route ('feed' or 'discover').
        current_user (User): The current authenticated user.
        search_query (str): The search query to filter posts (if any).
        page (int): The page number for pagination.

    Returns:
        Pagination: A paginated query result containing the requested posts.
    """
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
