# app/main/services.py

import asyncio
import os
import urllib.parse

from flask import current_app, flash
from flask_babel import _

from PIL import Image
from app import db
from app.favicon import get_favicon
from app.models import Post, PostPic, User
from app.openai import generate_link_summary
from app.utils import get_webpage_title, top_crop


async def create_post(form, current_user):
    folder_path = form.post_folder.data.strip()
    folder_path = (
        "/" if not folder_path or folder_path == "/" else folder_path.strip("/")
    )
    post_pic = form.post_pic.data
    if post_pic and post_pic != '':
        try:
            img = Image.open(post_pic)
            # Check for EXIF orientation and rotate if necessary
            if hasattr(img, "_getexif"):
                exif = img._getexif()
                if exif:
                    orientation = exif.get(0x0112)
                    if orientation:
                        if orientation == 3:
                            img = img.rotate(180, expand=True)
                        elif orientation == 6:
                            img = img.rotate(270, expand=True)
                        elif orientation == 8:
                            img = img.rotate(90, expand=True)

            # Center crop, resize, and compress the image to 155x155
            resized_picture = top_crop(img, (285, 285))
            post = PostPic(link=urllib.parse.quote(form.post_link.data),
                    body=form.post_body.data,
                    description=form.post_description.data.strip(),
                    folder_link=folder_path,
                    author=current_user)
            if not post.body:
                webpage_title = get_webpage_title(form.post_link.data)
                if webpage_title:
                    post.body = webpage_title
                elif OPENAI_API_KEY:
                    post.body = generate_link_summary(post.link, OPENAI_API_KEY).rstrip(".")
            db.session.add(post)
            db.session.commit()
            post_pic_filename = f"{current_user.id}_{post.id}"
            resized_picture.save(
                os.path.join("app/static/post_pics", f"{post_pic_filename}.jpg"),
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

    OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]
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


async def get_posts_query(route_type, current_user, search_query, page):
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
