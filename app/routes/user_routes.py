import asyncio
import os
import urllib.parse

import markdown
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import _
from flask_login import current_user
from sqlalchemy import or_, union_all

from app import db
from app.constants import POST_PICS_PATH
from app.favicon import get_favicon
from app.helpers.route_helpers import handle_route
from app.main.forms import EmptyForm, PostForm
from app.models import Leaf, Post, PostPic, User
from app.openai import generate_link_summary
from app.utils import get_webpage_title, image_preprocessing, is_subpath, top_crop

bp = Blueprint("user", __name__)
# user_visit_counter_dict = {}


@bp.route("/p/<username>", methods=["POST", "GET"])
@bp.route("/p/<username>/", methods=["POST", "GET"])
async def user_pics(username):
    user = User.query.filter(User.username.ilike(username)).first_or_404()
    followers = user.followers

    if current_user.get_id():
        is_following = current_user in followers
    else:
        is_following = False

    if user.private_mode and user != current_user and not is_following:
        return render_template("user_private.html", user=user, form=EmptyForm())

    return await handle_route("user_pics", user=user, username=username)


@bp.route("/p/<username>/<path:path>", methods=["POST", "GET"])
async def user_pics_subfolder(username, path):
    user = User.query.filter(User.username.ilike(username)).first_or_404()
    followers = user.followers
    empty_form = EmptyForm()
    OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]

    form = PostForm()

    if request.method == "POST" and form.validate_on_submit():
        folder_path = form.post_folder.data.strip()
        if folder_path and folder_path != "/":
            folder_path = folder_path.strip("/")
        else:
            folder_path = path
        folder_path = folder_path if form.post_folder.data else path
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
                        post.body = generate_link_summary(
                            post.link, OPENAI_API_KEY
                        ).rstrip(".")
                db.session.add(post)
                db.session.commit()
                post_pic_filename = f"{current_user.id}_{post.id}"
                resized_picture.save(
                    os.path.join(POST_PICS_PATH, f"{post_pic_filename}.jpg"),
                    "JPEG",
                )
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"message": "Your link is now posted!"}), 200
                flash(_("Your link is now posted!"))
                return redirect(url_for("user.user_pic_subfolder"))
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
        # If post.body is None, try to set it to the webpage title
        if not post.body:
            webpage_title = get_webpage_title(form.post_link.data)
            if webpage_title:
                post.body = webpage_title
            elif OPENAI_API_KEY:
                post.body = generate_link_summary(post.link, OPENAI_API_KEY).rstrip(".")
        favicon_file_name = await asyncio.wait_for(get_favicon(post.link), 8)
        if favicon_file_name:
            post.favicon_file_name = favicon_file_name

        db.session.add(post)
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"message": "Your link is now posted!"}), 200
        flash(_("Your link is now posted!"))
        return redirect(url_for("user.user_pic_subfolder"))
    elif request.method == "POST":
        # If it's a POST request but validation failed, return errors as JSON
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(form.errors), 400
        # For non-AJAX requests, render the template with errors
        return render_template(
            "user_pic_subfolder.html", title=_(user.username), form=form
        )

    if current_user.get_id():
        is_following = current_user in followers
    else:
        is_following = False
    if user.private_mode == True and user != current_user and not is_following:
        return render_template("user_private.html", user=user, form=empty_form)
    else:
        splitPath = path.rstrip("/").rsplit("/", 1)
        prevPath = splitPath[0]
        current_folder = splitPath[-1]
        if len(path.split("/")) <= 1:
            user_home_page = True
        else:
            user_home_page = False

        posts = user.pic_posts.filter_by(folder_link=path).order_by(
            PostPic.timestamp.desc()
        )
        folders_tmp = (
            user.pic_posts.filter(PostPic.folder_link != path)
            .order_by(PostPic.timestamp.desc())
            .all()
        )
        folders = []
        visited_folders = []
        for post in folders_tmp:
            if not is_subpath(path, post.folder_link):
                continue
            else:
                post.folder_name = (
                    post.folder_link.removeprefix(path).strip("/").split("/")[0]
                )
                post.folder_link = path + "/" + post.folder_name
                if post.folder_name != "" and post.folder_name not in visited_folders:
                    visited_folders.append(post.folder_name)
                    folders.append(post)

        return render_template(
            "user_pics_subfolder.html",
            user=user,
            posts=posts,
            form=form,
            path=path,
            empty_form=empty_form,
            folders=folders,
            prevPath=prevPath,
            user_home_page=user_home_page,
            current_folder=current_folder,
        )


# USER PROFILE ROUTE NEEDS TO BE AT BOTTOM SINCE IT ACTS AS A CATCH ALL ROUTE
@bp.route("/<username>/", methods=["POST", "GET"])
@bp.route("/<username>", methods=["POST", "GET"])
async def user(username):
    user = User.query.filter(User.username.ilike(username)).first_or_404()
    followers = user.followers
    OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]

    form = PostForm()
    if request.method == "POST" and form.validate_on_submit():
        folder_path = form.post_folder.data.strip()
        if folder_path and folder_path != "/":
            folder_path = folder_path.strip("/")
        else:
            folder_path = "/"
        folder_path = folder_path if form.post_folder.data else "/"
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
                        post.body = generate_link_summary(
                            post.link, OPENAI_API_KEY
                        ).rstrip(".")
                db.session.add(post)
                db.session.commit()
                post_pic_filename = f"{current_user.id}_{post.id}"
                resized_picture.save(
                    os.path.join(POST_PICS_PATH, f"{post_pic_filename}.jpg"),
                    "JPEG",
                )
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"message": "Your link is now posted!"}), 200
                flash(_("Your link is now posted!"))
                return redirect(url_for("user.user"))
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
        # If post.body is None, try to set it to the webpage title
        if not post.body:
            webpage_title = get_webpage_title(form.post_link.data)
            if webpage_title:
                post.body = webpage_title
            elif OPENAI_API_KEY:
                post.body = generate_link_summary(post.link, OPENAI_API_KEY).rstrip(".")
        favicon_file_name = await asyncio.wait_for(get_favicon(post.link), 8)
        if favicon_file_name:
            post.favicon_file_name = favicon_file_name

        db.session.add(post)
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"message": "Your link is now posted!"}), 200
        flash(_("Your link is now posted!"))
        return redirect(url_for("user.user"))
    elif request.method == "POST":
        # If it's a POST request but validation failed, return errors as JSON
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(form.errors), 400
        # For non-AJAX requests, render the template with errors
        return render_template("user.html", title=_(user.username), form=form)

    empty_form = EmptyForm()

    if current_user.get_id():
        is_following = current_user in followers
    else:
        is_following = False
    if user.private_mode == True and user != current_user and not is_following:
        return render_template("user_private.html", user=user, form=empty_form)
    else:
        shared_id_list = []
        posts = user.posts.filter_by(folder_link="/").order_by(Post.timestamp.desc())

        # BEGIN OUTBOUND SHARE CODE
        outbound_shares = user.outbound_shares
        for share in outbound_shares:
            sharer_folder_path = share.sharer_folder_path
            if len(sharer_folder_path.split("/", 1)) > 1:
                continue
            sharer = User.query.filter_by(id=share.sharer_id).first_or_404()
            if sharer is None:
                continue
            sharer_posts = sharer.posts.filter(
                or_(
                    Post.folder_link.like(sharer_folder_path + "/%"),
                    Post.folder_link == sharer_folder_path,
                )
            )
            for post in sharer_posts:
                shared_id_list.append(post.id)

        # END OUTBOUND SHARE CODE

        # CHECK FOR INBOUND SHARES
        if current_user == user:
            inbound_shares = current_user.inbound_shares
            shared_folders_list = []

            for share in inbound_shares:
                sharer_folder_path = share.sharer_folder_path
                sharee_folder_path = share.sharee_folder_path
                sharer = User.query.filter_by(id=share.sharer_id).first_or_404()
                if sharer is None:
                    continue
                sharer_posts = sharer.posts.filter(
                    or_(
                        Post.folder_link.like(sharer_folder_path + "/%"),
                        Post.folder_link == sharer_folder_path,
                    )
                )

                for post in sharer_posts:
                    if sharee_folder_path == "/":
                        post.folder_link = post.folder_link
                    else:
                        post.folder_link = sharee_folder_path + "/" + post.folder_link
                    post.author = current_user
                    post.user_id = current_user.id
                    shared_id_list.append(post.id)

            if shared_folders_list:
                original_query = user.posts.filter_by(folder_link="/")
                shared_folders_list.append(original_query)

                combined_query = union_all(*shared_folders_list)
                post_list = db.session.execute(combined_query).all()
                id_list = [post[0] for post in post_list]

                posts = (
                    Post.query.filter(Post.id.in_(id_list))
                    .order_by(Post.timestamp.desc())
                    .all()
                )
        # END INBOUND SHARE CODE

        page = request.args.get("page", 1, type=int)
        posts = posts.paginate(
            page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
        )

        next_url = (
            url_for("user.user", username=user.username, page=posts.next_num)
            if posts.has_next
            else None
        )
        prev_url = (
            url_for("user.user", username=user.username, page=posts.prev_num)
            if posts.has_prev
            else None
        )

        # Calculate current_page and total_pages
        current_page = posts.page
        total_pages = posts.pages or 1

        folders_tmp = (
            user.posts.filter(Post.folder_link != "/")
            .order_by(Post.timestamp.desc())
            .all()
        )
        folders = []
        visited_folders = []

        for post in folders_tmp:
            post.folder_name = post.folder_link = post.folder_link.split("/")[0]
            if shared_id_list:
                post.is_shared = True if post.id in shared_id_list else False
            if post.folder_name != "" and post.folder_name not in visited_folders:
                visited_folders.append(post.folder_name)
                folders.append(post)

        # user_visit_counter_dict[f"user_{user.id}"] = (
        #     user_visit_counter_dict.get(f"user_{user.id}", 0) + 1
        # )
        return render_template(
            "user.html",
            user=user,
            title=_(user.username),
            posts=posts.items,
            next_url=next_url,
            prev_url=prev_url,
            form=form,
            empty_form=empty_form,
            folders=folders,
            current_page=current_page,
            total_pages=total_pages,
        )


@bp.route("/<username>/<path:path>", methods=["POST", "GET"])
async def user_subfolder(username, path):
    user = User.query.filter(User.username.ilike(username)).first_or_404()
    followers = user.followers
    empty_form = EmptyForm()
    OPENAI_API_KEY = current_app.config["OPENAI_API_KEY"]
    form = PostForm()

    if request.method == "POST" and form.validate_on_submit():
        folder_path = form.post_folder.data.strip()
        if folder_path and folder_path != "/":
            folder_path = folder_path.strip("/")
        else:
            folder_path = path
        folder_path = folder_path if form.post_folder.data else path
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
                        post.body = generate_link_summary(
                            post.link, OPENAI_API_KEY
                        ).rstrip(".")
                db.session.add(post)
                db.session.commit()
                post_pic_filename = f"{current_user.id}_{post.id}"
                resized_picture.save(
                    os.path.join(POST_PICS_PATH, f"{post_pic_filename}.jpg"),
                    "JPEG",
                )
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"message": "Your link is now posted!"}), 200
                flash(_("Your link is now posted!"))
                return redirect(url_for("user.user_subfolder"))
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
        # If post.body is None, try to set it to the webpage title
        if not post.body:
            webpage_title = get_webpage_title(form.post_link.data)
            if webpage_title:
                post.body = webpage_title
            elif OPENAI_API_KEY:
                post.body = generate_link_summary(post.link, OPENAI_API_KEY).rstrip(".")
        favicon_file_name = await asyncio.wait_for(get_favicon(post.link), 8)
        if favicon_file_name:
            post.favicon_file_name = favicon_file_name

        db.session.add(post)
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"message": "Your link is now posted!"}), 200
        flash(_("Your link is now posted!"))
        return redirect(url_for("user.user_subfolder"))
    elif request.method == "POST":
        # If it's a POST request but validation failed, return errors as JSON
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(form.errors), 400
        # For non-AJAX requests, render the template with errors
        return render_template("user_subfolder.html", title=_(user.username), form=form)

    if current_user.get_id():
        is_following = current_user in followers
    else:
        is_following = False
    if user.private_mode == True and user != current_user and not is_following:
        return render_template("user_private.html", user=user, form=empty_form)
    else:
        splitPath = path.rstrip("/").rsplit("/", 1)
        prevPath = splitPath[0]
        current_folder = splitPath[-1]
        if len(path.split("/")) <= 1:
            user_home_page = True
        else:
            user_home_page = False

        if user.leafs:
            if path != "/":
                for leaf in user.leafs:
                    file_name = leaf.file_name
                    if current_folder == file_name:
                        get_leaf = Leaf.query.filter_by(
                            user_id=user.id, folder_path=prevPath, file_name=file_name
                        ).first()
                        if get_leaf is None:
                            continue
                        else:
                            rendered_content = markdown.markdown(get_leaf.md_text)
                            return render_template(
                                "leaf_page.html",
                                user=user,
                                form=empty_form,
                                user_home_page=user_home_page,
                                rendered_content=rendered_content,
                                prevPath=prevPath,
                            )

        shared_id_list = []

        # BEGIN OUTBOUND SHARE CODE
        outbound_shares = user.outbound_shares
        for share in outbound_shares:
            sharer_folder_path = share.sharer_folder_path
            sharer = User.query.filter_by(id=share.sharer_id).first_or_404()
            if sharer is None:
                continue
            sharer_posts = sharer.posts.filter(
                or_(
                    Post.folder_link.like(sharer_folder_path + "/%"),
                    Post.folder_link == sharer_folder_path,
                )
            )
            for post in sharer_posts:
                shared_id_list.append(post.id)

        # END OUTBOUND SHARE CODE

        # CHECK FOR INBOUND SHARES
        if current_user == user:
            inbound_shares = current_user.inbound_shares
            shared_folders_list = []

            for share in inbound_shares:
                sharer_folder_path = share.sharer_folder_path
                sharee_folder_path = share.sharee_folder_path
                sharer = User.query.filter_by(id=share.sharer_id).first_or_404()
                if sharer is None:
                    continue
                sharer_posts = sharer.posts.filter(
                    or_(
                        Post.folder_link.like(sharer_folder_path + "/%"),
                        Post.folder_link == sharer_folder_path,
                    )
                )

                for post in sharer_posts:
                    if sharee_folder_path == "/":
                        post.folder_link = post.folder_link
                    else:
                        post.folder_link = sharee_folder_path + "/" + post.folder_link
                    post.author = current_user
                    post.user_id = current_user.id
                    shared_id_list.append(post.id)

                filtered_sharer_posts = sharer_posts.filter_by(folder_link=path)

                shared_folders_list.append(filtered_sharer_posts)

            if shared_folders_list:
                original_query = user.posts.filter_by(folder_link=path)
                shared_folders_list.append(original_query)

                combined_query = union_all(*shared_folders_list)
                post_list = db.session.execute(combined_query).all()
                id_list = [post[0] for post in post_list]

                posts = (
                    Post.query.filter(Post.id.in_(id_list))
                    .order_by(Post.timestamp.desc())
                    .all()
                )

            else:
                posts = user.posts.filter_by(folder_link=path).order_by(
                    Post.timestamp.desc()
                )

            folders_tmp = (
                user.posts.filter(Post.folder_link != path)
                .order_by(Post.timestamp.desc())
                .all()
            )
            folders = []
            visited_folders = []
            for post in folders_tmp:
                if not is_subpath(path, post.folder_link):
                    continue
                else:
                    post.folder_name = (
                        post.folder_link.removeprefix(path).strip("/").split("/")[0]
                    )
                    post.folder_link = path + "/" + post.folder_name
                    if shared_id_list:
                        post.is_shared = True if post.id in shared_id_list else False
                    if (
                        post.folder_name != ""
                        and post.folder_name not in visited_folders
                    ):
                        visited_folders.append(post.folder_name)
                        folders.append(post)

            return render_template(
                "user_subfolder.html",
                user=user,
                title=_(user.username),
                posts=posts,
                empty_form=empty_form,
                form=form,
                folders=folders,
                prevPath=prevPath,
                user_home_page=user_home_page,
                current_folder=current_folder,
                path=path,
            )
        # END INBOUND SHARE CODE

        posts = user.posts.filter_by(folder_link=path).order_by(Post.timestamp.desc())
        folders_tmp = (
            user.posts.filter(Post.folder_link != path)
            .order_by(Post.timestamp.desc())
            .all()
        )
        folders = []
        visited_folders = []
        for post in folders_tmp:
            if not is_subpath(path, post.folder_link):
                continue
            else:
                post.folder_name = (
                    post.folder_link.removeprefix(path).strip("/").split("/")[0]
                )
                post.folder_link = path + "/" + post.folder_name
                if shared_id_list:
                    post.is_shared = True if post.id in shared_id_list else False
                if post.folder_name != "" and post.folder_name not in visited_folders:
                    visited_folders.append(post.folder_name)
                    folders.append(post)
        return render_template(
            "user_subfolder.html",
            user=user,
            title=_(user.username),
            posts=posts,
            form=form,
            empty_form=empty_form,
            folders=folders,
            prevPath=prevPath,
            user_home_page=user_home_page,
            current_folder=current_folder,
        )
