import os
from datetime import datetime
from functools import wraps

from flask import (
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import _, get_locale
from flask_login import current_user, login_required
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from app import db
from app.constants import FORBIDDEN_USERNAMES, POST_PICS_PATH, PROFILE_PICS_PATH
from app.favicon import hash_profile_pic
from app.helpers.route_helpers import handle_route
from app.main import bp
from app.main.forms import (
    CopyFolder,
    EmptyForm,
    MoveFolder,
    PageDownForm,
    RenameFolder,
    SearchForm,
    SettingsForm,
    ShareFolderForm,
)
from app.models import Leaf, Post, PostPic, ShareFolder, ShareFolderRequest, User
from app.utils import (
    copy_folder_util,
    image_preprocessing,
    is_subpath,
    move_folder_util,
    rename_folder_util,
    top_crop,
    validate_folder_path,
)

# user_visit_counter_dict = {}


@bp.before_app_request
def before_request():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.utcnow()
        db.session.commit()
    g.search_form = SearchForm()
    g.locale = str(get_locale())


def handle_ajax_request(f):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        try:
            return await f(*args, **kwargs)
        except Exception as e:
            current_app.logger.error(f"An error occurred: {str(e)}", exc_info=True)
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"error": "An unexpected error occurred"}), 500
            flash(_("An unexpected error occurred"))
            return redirect(url_for(f"main.{f.__name__}"))

    return decorated_function


@bp.route("/home", methods=["GET"])
@bp.route("/", methods=["GET"])
def home():
    return render_template("home.html", title=_("Home"))


@bp.route("/", methods=["GET", "POST"])
@bp.route("/feed", methods=["GET", "POST"])
@login_required
@handle_ajax_request
async def feed():
    return await handle_route(route_type="feed")


@bp.route("/discover/", methods=["GET", "POST"])
@bp.route("/discover", methods=["GET", "POST"])
@handle_ajax_request
async def discover():
    return await handle_route(route_type="discover")


def get_cache_key(route_type, page, search_query):
    if current_user.is_authenticated:
        return f"{route_type}:user:{current_user.id}:{page}:{search_query}"
    return f"{route_type}:anon:{page}:{search_query}"


@bp.route("/post/delete/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    if current_user.inbound_shares:
        for share in current_user.inbound_shares:
            sharer_posts = Post.query.filter_by(user_id=share.sharer_id).all()
            for post in sharer_posts:
                if post.id == post_id:
                    # Delete associated Leaf objects
                    Leaf.query.filter_by(post_id=post_id).delete()
                    db.session.delete(post)
                    db.session.commit()
                    flash("Link deleted")
                    return redirect(request.referrer)

    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id == post.user_id:
        # Delete associated Leaf objects
        Leaf.query.filter_by(post_id=post_id).delete()
        db.session.delete(post)
        db.session.commit()
        flash("Link deleted")
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)


@bp.route("/post_pic/delete/<int:post_id>", methods=["POST"])
@login_required
def delete_post_pic(post_id):
    post = PostPic.query.filter_by(id=post_id).first_or_404()
    if current_user.id == post.user_id:
        # Delete associated Leaf objects
        db.session.delete(post)
        db.session.commit()

        # delete file
        file = os.path.join(POST_PICS_PATH, f"{post.user_id}_{post_id}.jpg")
        if os.path.exists(file):
            os.remove(file)
        flash("Link deleted")
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)


@bp.route("/account/delete/<int:user_id>", methods=["POST"])
@login_required
def delete_account(user_id):
    user = User.query.filter_by(id=user_id).first_or_404()
    if current_user.id == user.id:
        db.session.delete(user)
        db.session.commit()
        flash("Account deleted")
        redirect_url = url_for("auth.login")
        return jsonify(
            {"message": "Account deleted successfully", "redirect_url": redirect_url}
        )


@bp.route("/folder/delete/<path:folder_link>", methods=["POST"])
@login_required
def delete_folder(folder_link):
    print(folder_link)
    print("folder link!!!")
    posts = Post.query.filter_by(user_id=current_user.id).all()
    if current_user.inbound_shares:
        for share in current_user.inbound_shares:
            sharee_folder_path = share.sharee_folder_path
            sharer_posts = Post.query.filter_by(user_id=share.sharer_id).all()
            for post in sharer_posts:
                if post.folder_link != None and is_subpath(
                    folder_link.lstrip(sharee_folder_path).strip("/"), post.folder_link
                ):
                    db.session.delete(post)
    for post in posts:
        if (
            post.folder_link != None
            and is_subpath(folder_link, post.folder_link)
            and current_user.id == post.user_id
        ):
            db.session.delete(post)

    leaves = Leaf.query.filter_by(user_id=current_user.id).all()
    for leaf in leaves:
        if is_subpath(folder_link, leaf.folder_path):
            db.session.delete(leaf)

    db.session.commit()
    flash(f"Folder '{folder_link}' deleted")

    previous_folder = folder_link.rstrip("/").rsplit("/", 1)[0]
    if len(folder_link.split("/")) <= 1:
        previous_folder = "/"
    return (
        redirect(url_for("user.user", username=current_user.username))
        if previous_folder == "/"
        else redirect(
            url_for(
                "user.user_subfolder",
                username=current_user.username,
                path=previous_folder,
            )
        )
    )


@bp.route("/pic_folder/delete/<path:folder_link>", methods=["POST"])
@login_required
def delete_pic_folder(folder_link):
    posts = PostPic.query.filter_by(user_id=current_user.id).all()
    for post in posts:
        if (
            post.folder_link != None
            and is_subpath(folder_link, post.folder_link)
            and current_user.id == post.user_id
        ):
            db.session.delete(post)
            file = os.path.join(POST_PICS_PATH, f"{post.user_id}_{post.id}.jpg")
            if os.path.exists(file):
                os.remove(file)

    db.session.commit()
    flash(f"Folder '{folder_link}' deleted from {current_user.toggle_name}")

    previous_folder = folder_link.rstrip("/").rsplit("/", 1)[0]
    if len(folder_link.split("/")) <= 1:
        previous_folder = "/"
    return (
        redirect(url_for("user.user_pics", username=current_user.username))
        if previous_folder == "/"
        else redirect(
            url_for(
                "user.user_pics_subfolder",
                username=current_user.username,
                path=previous_folder,
            )
        )
    )


@bp.route("/post/favorite/<int:post_id>", methods=["POST"])
@login_required
def favorite_post(post_id):
    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id != post.user_id:
        current_user.favorite(post)
        db.session.commit()
        flash("Link added to favorites")
        return redirect(request.referrer)


@bp.route("/post/unfavorite/<int:post_id>", methods=["POST"])
@login_required
def unfavorite_post(post_id):
    post = Post.query.filter_by(id=post_id).first_or_404()
    if current_user.id != post.user_id:
        current_user.unfavorite(post)
        db.session.commit()
        return redirect(request.referrer)


# @bp.route("/stats/user_visit_counts")
# def visit_counts():
#     return jsonify(user_visit_counter_dict)


@bp.route("/followers/<username>")
def get_followers(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    followers = user.followers.order_by(User.id.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )

    next_url = (
        url_for("main.get_followers", username=user.username, page=followers.next_num)
        if followers.has_next
        else None
    )
    prev_url = (
        url_for("main.get_followers", username=user.username, page=followers.prev_num)
        if followers.has_prev
        else None
    )
    return render_template(
        "followers.html",
        user=user,
        followers=followers,  # Pass the whole pagination object
        next_url=next_url,
        prev_url=prev_url,
    )


@bp.route("/following/<username>")
def get_following(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    following = user.followed.order_by(User.id.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )

    next_url = (
        url_for("main.get_following", username=user.username, page=following.next_num)
        if following.has_next
        else None
    )
    prev_url = (
        url_for("main.get_following", username=user.username, page=following.prev_num)
        if following.has_prev
        else None
    )
    return render_template(
        "following.html",
        user=user,
        following=following,  # Pass the whole pagination object
        next_url=next_url,
        prev_url=prev_url,
    )


@bp.route("/favorites/<username>")
def get_favorites(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    favorites = user.favorites.order_by(Post.id.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    next_url = (
        url_for("main.get_favorites", username=user.username, page=favorites.next_num)
        if favorites.has_next
        else None
    )
    prev_url = (
        url_for("main.get_favorites", username=user.username, page=favorites.prev_num)
        if favorites.has_prev
        else None
    )
    return render_template(
        "favorites.html",
        user=user,
        posts=favorites,
        next_url=next_url,
        prev_url=prev_url,
    )


@bp.route("/follow_requests/<username>")
def get_follow_requests(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    requests = user.get_follow_requestors().paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    next_url = (
        url_for(
            "main.get_follow_requests", username=user.username, page=requests.next_num
        )
        if requests.has_next
        else None
    )
    prev_url = (
        url_for(
            "main.get_follow_requests", username=user.username, page=requests.prev_num
        )
        if requests.has_prev
        else None
    )
    form = EmptyForm()

    return render_template(
        "follow_requests.html",
        user=user,
        requestors=requests.items,
        next_url=next_url,
        prev_url=prev_url,
        form=form,
    )


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    form = SettingsForm(current_user.username, current_user.email)
    if form.validate_on_submit():
        current_user.display_name = form.display_name.data.strip()
        current_user.username = form.username.data.strip()
        current_user.email = form.email.data.strip()
        current_user.about_me = form.about_me.data.strip()
        current_user.private_mode = form.private_mode.data
        current_user.dark_mode = form.dark_mode.data
        current_user.description_text_color = form.description_text_color.data
        current_user.toggle_color = form.toggle_color.data
        current_user.toggle_name = form.toggle_name.data
        profile_pic = form.profile_pic.data

        try:
            if profile_pic:
                tmp_filename = current_user.username + secure_filename(
                    profile_pic.filename
                )
                filename = hash_profile_pic(tmp_filename)
                old_profile_pic = None
                if current_user.profile_pic:
                    old_profile_pic = current_user.profile_pic.rstrip(".jpg")
                try:
                    current_user.profile_pic = f"{filename}.jpg"
                    current_app.logger.info(
                        f"Assigned profile_pic: {current_user.profile_pic}"
                    )

                    img = image_preprocessing(profile_pic)
                    # Center crop, resize, and compress the image to 155x155
                    resized_picture = top_crop(img, (155, 155))
                    resized_picture.save(
                        os.path.join(PROFILE_PICS_PATH, f"{filename}.jpg"),
                        "JPEG",
                    )

                    # Center crop, resize, and compress to 25x25
                    resized_picture_mini = top_crop(img, (25, 25))
                    resized_picture_mini.save(
                        os.path.join(PROFILE_PICS_PATH, f"{filename}_mini_25.jpg"),
                        "JPEG",
                    )

                    # Delete old pics
                    if old_profile_pic:
                        files_to_delete = [
                            os.path.join(
                                PROFILE_PICS_PATH,
                                f"{old_profile_pic}_mini_25.jpg",
                            ),
                            os.path.join(PROFILE_PICS_PATH, f"{old_profile_pic}.jpg"),
                        ]
                        for file in files_to_delete:
                            if os.path.exists(file):
                                os.remove(file)
                except Exception as e:
                    current_app.logger.error(f"Exception occurred. {e}")
                    flash(_("Error in uploading image. Please try again"), "error")
                    return redirect(url_for("main.settings"))
        except RequestEntityTooLarge as e:
            current_app.logger.error(f"Exception occurred. {e}")
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return (
                    jsonify({"error": "The file is too large. Maximum size is 1MB."}),
                    413,
                )
            flash(_("The file is too large. Maximum size is 1MB."), "error")
            return redirect(url_for("main.settings"))

        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            # flash(_("Your changes have been saved."))
            return jsonify({"message": "Your changes have been saved."}), 200
        return redirect(url_for("main.settings"))

    elif request.method == "GET":
        form.display_name.data = current_user.display_name
        form.username.data = current_user.username
        form.email.data = current_user.email
        form.about_me.data = current_user.about_me
        form.private_mode.data = current_user.private_mode
        form.dark_mode.data = current_user.dark_mode
        form.description_text_color.data = current_user.description_text_color
        form.toggle_color.data = current_user.toggle_color
        form.toggle_name.data = (
            current_user.toggle_name if current_user.toggle_name else "pics"
        )

    return render_template(
        "settings.html",
        title=_("Settings"),
        form=form,
        forbidden_usernames=FORBIDDEN_USERNAMES,
    )


@bp.route("/shared_folders", methods=["GET", "POST"])
@login_required
def shared_folders():
    share_folder_form = ShareFolderForm(current_user.username)
    if share_folder_form.validate_on_submit():
        recipients = share_folder_form.recipients.data.strip().split(",")
        folder_path = share_folder_form.folder_path.data.strip().strip("/")
        sent_request = False
        for recipient in recipients:
            user = User.query.filter_by(username=recipient.strip()).first_or_404()
            if not current_user.is_share_requested(user, folder_path):
                new_request = ShareFolderRequest(
                    requestor_id=current_user.id,
                    requestee_id=user.id,
                    shared_folder_path=folder_path,
                )
                db.session.add(new_request)
                db.session.commit()
                sent_request = True
        if sent_request:
            flash(_("Outbound share request sent."))
        return redirect(request.referrer)
    inbound_shares = current_user.inbound_shares
    outbound_shares = current_user.outbound_shares
    return render_template(
        "shared_folders.html",
        title=_("Shared Folders"),
        share_folder_form=share_folder_form,
        inbound_shares=inbound_shares,
        outbound_shares=outbound_shares,
        username=current_user.username,
    )


@bp.route("/share_requests_received/<username>")
def get_share_requests_received(username):
    user = User.query.filter_by(username=username).first_or_404()
    page = request.args.get("page", 1, type=int)
    requests = user.share_requests_received.paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    next_url = (
        url_for(
            "main.get_share_requests_received",
            username=user.username,
            page=requests.next_num,
        )
        if requests.has_next
        else None
    )
    prev_url = (
        url_for(
            "main.get_share_requests_received",
            username=user.username,
            page=requests.prev_num,
        )
        if requests.has_prev
        else None
    )
    form = EmptyForm()
    return render_template(
        "share_requests_received.html",
        user=user,
        requests=requests.items,
        next_url=next_url,
        prev_url=prev_url,
        form=form,
    )


@bp.route(
    "/accept_share/<int:requestee_id>/<int:requestor_id>/<path:request_folder>",
    methods=["POST"],
)
@login_required
def accept_share(requestee_id, requestor_id, request_folder):
    if current_user.id == requestee_id:
        form = EmptyForm()
        if form.validate_on_submit():
            mount_path = request.form.get("mount_path").strip()
            if mount_path == "":
                mount_path = "/"
            if mount_path != "/":
                mount_path = mount_path.strip("/")
                if len(mount_path) > 255:
                    flash(_("Mount path must be 255 characters or less"))
                    return redirect(request.referrer)
                posts = current_user.posts.all()
                filtered_posts = filter(
                    lambda post: is_subpath(mount_path, post.folder_link), posts
                )
                filtered_posts_list = list(filtered_posts)
                if not filtered_posts_list:
                    flash(_("Mount folder path does not exist"))
                    return redirect(request.referrer)
            requestor = User.query.filter_by(id=requestor_id).first()
            if requestor is None:
                flash(_("User not found."))
            share_request = ShareFolderRequest.query.filter_by(
                requestor_id=requestor.id,
                requestee_id=requestee_id,
                shared_folder_path=request_folder,
            ).first()
            if share_request is None:
                flash(_("Share request not found."))
            sharer_id = requestor.id
            sharee_id = current_user.id
            sharer_folder_path = request_folder
            sharee_folder_path = mount_path
            if not current_user.is_share(
                sharer_id, sharer_folder_path, sharee_folder_path
            ):
                new_share = ShareFolder(
                    sharer_id,
                    sharee_id,
                    sharer_folder_path,
                    sharee_folder_path,
                )
                db.session.add(new_share)
                db.session.delete(share_request)
                db.session.commit()
                flash(_("New Inbound Share Added."))
                return redirect(request.referrer)
        else:
            return redirect(request.referrer)
    return redirect(request.referrer)


@bp.route(
    "/decline_share/<int:requestee_id>/<int:requestor_id>/<path:request_folder>",
    methods=["POST"],
)
@login_required
def decline_share(requestee_id, requestor_id, request_folder):
    if current_user.id == requestee_id:
        form = EmptyForm()
        if form.validate_on_submit():
            requestor = User.query.filter_by(id=requestor_id).first()
            if requestor is None:
                flash(_("User not found."))
                return redirect(request.referrer)
            if requestor == current_user:
                flash(_("You cannot decline yourself"))
                return redirect(request.referrer)
            share_request = ShareFolderRequest.query.filter_by(
                requestor_id=requestor.id,
                requestee_id=requestee_id,
                shared_folder_path=request_folder,
            ).first()
            if share_request is None:
                flash(_("Share request not found."))
                return redirect(request.referrer)
            db.session.delete(share_request)
            db.session.commit()
            return redirect(request.referrer)
        else:
            return redirect(request.referrer)
    return redirect(request.referrer)


@bp.route(
    "/remove_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>",
    methods=["POST"],
)
@bp.route(
    "/remove_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>/<path:sharee_folder_path>",
    methods=["POST"],
)
@login_required
def remove_inbound_share(
    sharee_id, sharer_id, sharer_folder_path, sharee_folder_path="/"
):
    if current_user.id == sharee_id:
        share = ShareFolder.query.filter_by(
            sharee_id=sharee_id,
            sharer_id=sharer_id,
            sharee_folder_path=sharee_folder_path,
            sharer_folder_path=sharer_folder_path,
        ).first()
        if share is None:
            flash(_("Share not found."))
            return redirect(request.referrer)
        db.session.delete(share)
        db.session.commit()
        flash(_("Inbound share removed"))
        return redirect(request.referrer)


@bp.route(
    "/remove_outbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>",
    methods=["POST"],
)
@bp.route(
    "/remove_outbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>/<path:sharee_folder_path>",
    methods=["POST"],
)
@login_required
def remove_outbound_share(
    sharee_id, sharer_id, sharer_folder_path, sharee_folder_path="/"
):
    if current_user.id == sharer_id:
        share = ShareFolder.query.filter_by(
            sharee_id=sharee_id,
            sharer_id=sharer_id,
            sharee_folder_path=sharee_folder_path,
            sharer_folder_path=sharer_folder_path,
        ).first()
        if share is None:
            flash(_("Share not found."))
            return redirect(request.referrer)
        db.session.delete(share)
        db.session.commit()
        flash(_("Outbound share removed"))
        return redirect(request.referrer)


@bp.route(
    "/update_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>",
    methods=["POST"],
)
@bp.route(
    "/update_inbound_share/<int:sharee_id>/<int:sharer_id>/<path:sharer_folder_path>/<path:sharee_folder_path>",
    methods=["POST"],
)
@login_required
def update_inbound_share(
    sharee_id, sharer_id, sharer_folder_path, sharee_folder_path="/"
):
    if request.method == "POST" and current_user.id == sharee_id:
        mount_path = request.form.get("mount_path").strip()
        if mount_path == "":
            mount_path = "/"
        if mount_path != "/":
            mount_path = mount_path.strip("/")
            if len(mount_path) > 255:
                flash(_("Mount path must be 255 characters or less"))
                return redirect(request.referrer)
            posts = current_user.posts.all()
            filtered_posts = filter(
                lambda post: is_subpath(mount_path, post.folder_link), posts
            )
            filtered_posts_list = list(filtered_posts)
            if not filtered_posts_list:
                flash(_("Mount folder path does not exist"))
                return redirect(request.referrer)
        share = ShareFolder.query.filter_by(
            sharee_id=sharee_id,
            sharer_id=sharer_id,
            sharee_folder_path=sharee_folder_path,
            sharer_folder_path=sharer_folder_path,
        ).first()
        if share is None:
            flash(_("Share not found."))
            return redirect(request.referrer)
        share.sharee_folder_path = mount_path
        db.session.commit()
        flash(_(f"Mount path changed to {mount_path}"))
        return redirect(request.referrer)


@bp.route("/remove_all_outbound_shares/<username>", methods=["POST"])
@login_required
def remove_all_outbound_shares(username):
    user = User.query.filter_by(username=username).first_or_404()
    if current_user.id == user.id:
        for share in user.outbound_shares:
            share_to_remove = ShareFolder.query.filter_by(
                sharee_id=share.sharee_id,
                sharer_id=share.sharer_id,
                sharee_folder_path=share.sharee_folder_path,
                sharer_folder_path=share.sharer_folder_path,
            ).first()
            db.session.delete(share_to_remove)
        db.session.commit()
        flash(_("Outbound shares removed"))
        return redirect(request.referrer)


@bp.route("/remove_all_inbound_shares/<username>", methods=["POST"])
@login_required
def remove_all_inbound_shares(username):
    user = User.query.filter_by(username=username).first_or_404()
    if current_user.id == user.id:
        for share in user.inbound_shares:
            share_to_remove = ShareFolder.query.filter_by(
                sharee_id=share.sharee_id,
                sharer_id=share.sharer_id,
                sharee_folder_path=share.sharee_folder_path,
                sharer_folder_path=share.sharer_folder_path,
            ).first()
            db.session.delete(share_to_remove)
        db.session.commit()
        flash(_("Inbound shares removed"))
        return redirect(request.referrer)


@bp.route("/follow/<username>", methods=["POST"])
@login_required
def follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot follow yourself!"))
            return redirect(url_for("user.user", username=username))
        current_user.follow(user)
        db.session.commit()
        flash(_("You are following %(username)s!", username=username))
        return redirect(url_for("user.user", username=username))
    else:
        return redirect(url_for("main.feed"))


@bp.route("/approve_follow/<username>", methods=["POST"])
@login_required
def approve_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot approve yourself"))
            return redirect(request.referrer)
        user.follow(current_user)
        user.unrequest_follow(current_user)
        db.session.commit()
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)


@bp.route("/deny_follow/<username>", methods=["POST"])
@login_required
def deny_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot deny yourself"))
            return redirect(request.referrer)
        user.unrequest_follow(current_user)
        db.session.commit()
        return redirect(request.referrer)
    else:
        return redirect(request.referrer)


@bp.route("/request_follow/<username>", methods=["POST"])
@login_required
def request_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot follow yourself!"))
            return redirect(url_for("user.user", username=username))
        current_user.request_follow(user)
        db.session.commit()
        flash(_("Requested to follow %(username)s!", username=username))
        return redirect(url_for("user.user", username=username))
    else:
        return redirect(url_for("main.feed"))


@bp.route("/cancel_request_follow/<username>", methods=["POST"])
@login_required
def cancel_request_follow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot cancel request for yourself!"))
            return redirect(url_for("user.user", username=username))
        current_user.unrequest_follow(user)
        db.session.commit()
        flash(_("Cancelled request to follow %(username)s!", username=username))
        return redirect(url_for("user.user", username=username))
    else:
        return redirect(url_for("main.feed"))


@bp.route("/unfollow/<username>", methods=["POST"])
@login_required
def unfollow(username):
    form = EmptyForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=username).first()
        if user is None:
            flash(_("User %(username)s not found.", username=username))
            return redirect(url_for("main.feed"))
        if user == current_user:
            flash(_("You cannot unfollow yourself!"))
            return redirect(url_for("user.user", username=username))
        current_user.unfollow(user)
        db.session.commit()
        flash(_("You are not following %(username)s.", username=username))
        return redirect(url_for("user.user", username=username))
    else:
        return redirect(url_for("main.feed"))


@bp.route("/search")
# @login_required
def search():
    query = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    users, total = User.search(query, page, current_app.config["USERS_PER_PAGE"])

    total_pages = (total - 1) // current_app.config["USERS_PER_PAGE"] + 1

    next_url = (
        url_for("search", q=query, page=page + 1)
        if total > page * current_app.config["USERS_PER_PAGE"]
        else None
    )
    prev_url = url_for("search", q=query, page=page - 1) if page > 1 else None

    return render_template(
        "search.html",
        title=_("Search"),
        users=users,
        next_url=next_url,
        prev_url=prev_url,
        query=query,
        current_page=page,
        total_pages=total_pages or 1,
    )


@bp.route("/actions", methods=["GET", "POST"])
@login_required
def actions():
    if request.method == "POST":
        form_type = request.form["form_type"]

        if form_type == "rename_folder_form":
            folder_path = request.form["folder_path"].strip()
            folder_name = request.form["new_folder_name"]
            if folder_path == "/" or not validate_folder_path(
                current_user.username, folder_path
            ):
                flash("Folder path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            if len(folder_name) > 45:
                flash(
                    "New folder name must be 45 characters or less. Try again", "error"
                )
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            rename_folder_util(
                current_user.username, folder_path.strip("/"), folder_name.strip()
            )
            flash(f"Folder was successfully renamed to {folder_name}")

        if form_type == "copy_folder_form":
            origin_path = request.form["origin_path"].strip()
            dest_path = request.form["dest_path"].strip()
            if origin_path != "/":
                origin_path.strip("/")
            if dest_path != "/":
                dest_path.strip("/")
            if not validate_folder_path(current_user.username, origin_path):
                flash("Origin path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            if not validate_folder_path(current_user.username, dest_path):
                flash("Destination path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            copy_folder_util(current_user, origin_path, dest_path)
            flash(f"Folder was successfully copied to {dest_path}")

        if form_type == "move_folder_form":
            origin_path = request.form["origin_path"].strip()
            dest_path = request.form["dest_path"].strip()
            if origin_path != "/":
                origin_path.strip("/")
            if dest_path != "/":
                dest_path.strip("/")
            if origin_path == "/" or not validate_folder_path(
                current_user.username, origin_path
            ):
                flash("Origin path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            if not validate_folder_path(current_user.username, dest_path):
                flash("Destination path is not valid. Try again", "error")
                return render_template(
                    "actions.html", title=_("Actions"), username=current_user.username
                )
            move_folder_util(current_user, origin_path, dest_path)
            flash(f"Folder was successfully moved to {dest_path}")

    return render_template(
        "actions.html", title=_("Actions"), username=current_user.username
    )


@bp.route("/privacy_policy")
def privacy_policy():
    return render_template("treetrav_profile/privacy_policy.html")


@bp.route("/about_us")
def about_us():
    return render_template("treetrav_profile/about_us.html")


@bp.route("/about_extension")
def about_extension():
    return render_template("treetrav_profile/about_extension.html")


@bp.route("/documentation/general")
def doc_general():
    return render_template("treetrav_profile/documentation/general.html")


@bp.route("/documentation/posts")
def doc_posts():
    return render_template("treetrav_profile/documentation/posts.html")


@bp.route("/documentation/shared_folders")
def doc_shared_folders():
    return render_template("treetrav_profile/documentation/shared_folders.html")


@bp.route("/documentation/leafs")
def doc_leafs():
    return render_template("treetrav_profile/documentation/leafs.html")


@bp.route("/documentation/actions")
def doc_actions():
    return render_template("treetrav_profile/documentation/actions.html")


# Action Routes ---------------------------------------------------------
@bp.route("/rename_folder", methods=["GET", "POST"])
def rename_folder():
    form = RenameFolder()
    return render_template("rename_folder.html", form=form)


@bp.route("/copy_folder", methods=["GET", "POST"])
def copy_folder():
    form = CopyFolder()
    return render_template("copy_folder.html", form=form)


@bp.route("/move_folder", methods=["GET", "POST"])
def move_folder():
    form = MoveFolder()
    return render_template("move_folder.html", form=form)


@bp.route("/create_leaf", methods=["GET", "POST"])
def create_leaf():
    form = PageDownForm()
    if request.method == "POST":
        md = request.form["pagedown"]
        folder_path = request.form["folder_path"].strip()
        file_name = request.form["file_name"].strip()
        if "/" in file_name:
            flash("Cannot have / in file name. Try again", "error")
            return render_template(
                "leaf_creator.html", form=form, username=current_user.username
            )
        if folder_path == "/":
            flash("Cannot create leaf page in home folder. Try again", "error")
            return render_template(
                "leaf_creator.html", form=form, username=current_user.username
            )
        else:
            folder_path = folder_path.strip("/")
        get_leaf = Leaf.query.filter_by(
            user_id=current_user.id, folder_path=folder_path, file_name=file_name
        ).first()
        if get_leaf:
            flash("Leaf with same name already exists at that path. Try again", "error")
            return render_template(
                "leaf_creator.html", form=form, username=current_user.username
            )

        # use prod domain if env is prod, else use local domain
        if current_app.config["IS_PROD"].lower() == "true":
            url = current_app.config["PROD_DOMAIN"]
        else:
            url = current_app.config["LOCAL_DOMAIN"]

        link = f"{url}/{current_user.username}/{folder_path}/{file_name}"

        # Create the Post first
        post = Post(
            link=link,
            body=file_name,
            folder_link=folder_path,
            author=current_user,
            favicon_file_name="leaf.png",
        )
        db.session.add(post)
        db.session.flush()  # This assigns an ID to the post without committing the transaction

        # Now create the Leaf with the post_id
        leaf = Leaf(
            user_id=current_user.id,
            folder_path=folder_path,
            file_name=file_name,
            md_text=md,
            post_id=post.id,  # Associate the Leaf with the Post
        )
        db.session.add(leaf)

        db.session.commit()  # Commit both the Post and Leaf to the database
        flash(f"Leaf page was successfully created @ {folder_path}")

    return render_template(
        "leaf_creator.html", form=form, username=current_user.username
    )


@bp.route("/api/suggestions")
def get_suggestions():
    query = request.args.get("q", "")
    if not query:
        return jsonify([])

    suggestions = User.get_suggestions(query)
    return jsonify(suggestions)


# FRONTEND AJAX RELATED ROUTES
@bp.route("/query/check_username", methods=["POST"])
def check_username():
    username = request.form["username"]
    user = User.query.filter(User.username.ilike(username.strip())).first()
    return jsonify({"exists": user is not None})


@bp.route("/query/check_email", methods=["POST"])
def check_email():
    email = request.form["email"]
    user = User.query.filter_by(email=email.strip()).first()
    return jsonify({"exists": user is not None})
